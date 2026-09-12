"""resolve_unlinked: a confident pick links and continues; otherwise a person is asked."""

from __future__ import annotations

from typing import Any

from sc_core.graph import cleared
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import Classification, SupplierCommsTask
from supplier_comms.models import InboundMeta, UnlinkedResolution
from supplier_comms.testing import SUPPLIER_EMAIL, FakePorts, demo_context
from tests.unit.graph.toy import FakeApprovalPorts

MSG = "AAMk-unlinked-1"


def _task(case_id: str, candidates: list[str]) -> SupplierCommsTask:
    return SupplierCommsTask(
        kind="resolve_unlinked",
        case_id=case_id,
        graph_message_id=MSG,
        candidate_po_names=candidates,
    )


def _prepare(ports: FakePorts) -> None:
    ports.contexts["P00016"] = demo_context(name="P00016")
    ports.inbound[MSG] = "Confirmamos las 20 mangueras de 1/2 para la orden de mangueras."
    ports.metas[MSG] = InboundMeta(
        graph_message_id=MSG, sender_address=SUPPLIER_EMAIL, web_link="https://outlook/x"
    )
    ports.partners[SUPPLIER_EMAIL] = 42


async def test_two_candidates_picks_one_links_and_continues(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    chat.responses.extend(
        [
            UnlinkedResolution(po_name="P00016", confidence=0.9, reason="menciona las mangueras"),
            Classification(kind="other", confidence=0.9, reason="acuse sin cambios"),
        ]
    )
    agent = make_agent()
    result = await agent.run(_task("case_u1", ["P00015", "P00016"]))
    assert result.kind == "resolve_unlinked" and result.chosen_po_name == "P00016"
    assert result.status == "no_action" and result.classification is not None
    assert ports.links == [
        {
            "po_id": 7,
            "graph_message_id": MSG,
            "direction": "in",
            "case_id": "case_u1",
            "confidence": "agent",
        }
    ]
    prompt = chat.calls[0].messages[1]["contents"][0]["text"]
    assert "P00015" in prompt and "P00016" in prompt and "mangueras" in prompt
    assert approval_ports.created == []
    snapshot = await agent.snapshot("case_u1")
    assert cleared(snapshot) and snapshot["task"]["po_name"] == "P00016"


async def test_no_pick_escalates_to_a_person_without_pausing(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    chat.responses.append(
        UnlinkedResolution(po_name=None, confidence=0.4, reason="no identifica la orden")
    )
    agent = make_agent()
    result = await agent.run(_task("case_u2", ["P00015", "P00016"]))
    assert result.status == "escalated" and result.outcome.approval_id == 101
    assert "escalado" in result.outcome.summary and ports.links == []
    created = approval_ports.created[0]
    assert created["kind"] == "unlinked_mail" and created["res_model"] == "res.partner"
    assert created["res_id"] == 42 and created["po_id"] is None
    assert created["payload"]["candidates"] == ["P00015", "P00016"]
    assert created["payload"]["sender_address"] == SUPPLIER_EMAIL
    assert approval_ports.reviews[0]["res_model"] == "res.partner"
    snapshot = await agent.snapshot("case_u2")
    assert cleared(snapshot) and snapshot["outcome"]["status"] == "escalated"


async def test_low_confidence_pick_is_not_trusted(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    chat.responses.append(UnlinkedResolution(po_name="P00016", confidence=0.5, reason="quizás"))
    result = await make_agent().run(_task("case_u3", ["P00015", "P00016"]))
    assert result.status == "escalated" and ports.links == []
    assert approval_ports.created[0]["kind"] == "unlinked_mail"


async def test_unknown_sender_without_candidates_escalates_without_model_call(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "Hola, ¿tienen stock de válvulas?"
    ports.metas[MSG] = InboundMeta(graph_message_id=MSG, sender_address="nobody@example.com")
    result = await make_agent().run(_task("case_u4", []))
    assert result.status == "escalated" and chat.calls == []
    created = approval_ports.created[0]
    assert created["res_id"] is None and created["payload"]["candidates"] == []
    assert approval_ports.reviews == []  # nothing to hang an activity on


async def test_candidates_come_from_the_sender_when_the_event_had_none(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    _prepare(ports)
    ports.open_orders = [{"name": "P00015", "state": "purchase"}]
    chat.responses.extend(
        [
            UnlinkedResolution(po_name="P00015", confidence=0.8, reason="única orden abierta"),
            Classification(kind="other", confidence=0.9, reason="acuse"),
        ]
    )
    result = await make_agent().run(_task("case_u5", []))
    assert result.chosen_po_name == "P00015" and ports.links[0]["po_id"] == 7


async def test_an_order_assigned_by_a_person_is_linked_without_the_model(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    ports.contexts["P00016"] = demo_context(name="P00016")
    ports.inbound[MSG] = "Buenas, adjuntamos la cotización solicitada."
    chat.responses.append(Classification(kind="other", confidence=0.9, reason="acknowledgement"))
    agent = make_agent()
    result = await agent.run(
        SupplierCommsTask(
            kind="resolve_unlinked",
            case_id="case_assigned",
            graph_message_id=MSG,
            candidate_po_names=["P00016"],
            assigned_po_name="P00016",
        )
    )
    assert result.status == "no_action"
    assert (
        ports.links[0]["po_id"] == ports.contexts["P00016"].id
        and ports.links[0]["direction"] == "in"
    )
    assert chat.calls[0].options["response_format"] != "UnlinkedResolution"  # never asked to pick
