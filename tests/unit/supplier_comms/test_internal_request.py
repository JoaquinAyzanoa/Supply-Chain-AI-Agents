"""An employee's email becomes RFQs after approval; the requester hears back at each step."""

from __future__ import annotations

from datetime import date
from typing import Any

from sc_core.graph import cleared
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import SupplierCommsTask
from supplier_comms.domain.models import InboundMeta, RequestedLine, RequestExtraction
from supplier_comms.testing import FakePorts, demo_context
from tests.unit.graph.toy import FakeApprovalPorts

MSG = "AAMk-internal-1"
REQUESTER = "ana.torres@empresa.com"


def _task(case_id: str) -> SupplierCommsTask:
    return SupplierCommsTask(kind="internal_request", case_id=case_id, graph_message_id=MSG)


def _prepare(ports: FakePorts) -> None:
    ports.inbound[MSG] = (
        "Hola compras, necesitamos 20 filtros hidráulicos HF-10 y 5 cajas de guantes de "
        "nitrilo para la semana del 21 de septiembre. Es para la parada de planta. Ana"
    )
    ports.metas[MSG] = InboundMeta(
        graph_message_id=MSG, sender_address=REQUESTER, web_link="https://outlook/int1"
    )
    ports.products_by_code["HF-10"] = (11, "[HF-10] Filtro hidráulico HF-10")
    ports.catalogue = [
        {"id": 12, "code": "GN-M", "name": "[GN-M] Guantes de nitrilo talla M"},
        {"id": 13, "code": "GL-C", "name": "[GL-C] Guantes de cuero"},
    ]
    ports.reference_suppliers[11] = {
        "partner_id": 42,
        "partner_name": "Proveedor Hidraulica",
        "price": 45.0,
        "currency": "USD",
    }
    ports.reference_suppliers[12] = {
        "partner_id": 10,
        "partner_name": "Importadora del Sur SAC",
        "price": 30.0,
        "currency": "USD",
    }


def _extraction() -> RequestExtraction:
    return RequestExtraction(
        items=[
            RequestedLine(description="filtros hidráulicos HF-10", product_ref="HF-10", qty=20),
            RequestedLine(description="guantes de nitrilo", qty=5, uom="cajas"),
            RequestedLine(description="banderines de seguridad", qty=3),
        ],
        need_date_raw="semana del 21 de septiembre",
        need_date=date(2026, 9, 21),
        requester_name="Ana",
        notes="parada de planta",
        confidence=0.9,
    )


async def test_the_request_is_matched_approved_and_becomes_one_rfq_per_supplier(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    chat.responses.append(_extraction())
    agent = make_agent()
    paused = await agent.run(_task("case_int1"))
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101
    created = approval_ports.created[0]
    assert created["kind"] == "internal_request" and created["requested_by"] == "supplier_comms"
    payload = created["payload"]
    assert payload["sender_address"] == REQUESTER and payload["need_date"] == "2026-09-21"
    assert "requester_name" not in payload  # names are never persisted
    items = payload["items"]
    assert [i["product_id"] for i in items] == [11, 12, None]
    assert (
        items[0]["match_confidence"] == 1.0 and items[0]["supplier_name"] == "Proveedor Hidraulica"
    )
    assert 0.6 <= items[1]["match_confidence"] < 1.0 and items[1]["supplier_id"] == 10
    assert payload["unmatched"] == 1 and payload["facts"]["amount"] == 1050.0
    assert "Internal request from ana.torres@empresa.com: 3 item(s)" in created["summary"]
    assert ports.created_rfqs == [] and ports.drafts == {}
    assert REQUESTER in chat.last_prompt_text() and "HF-10" in chat.last_prompt_text()

    done = await agent.resume(
        "case_int1",
        {
            "approval_id": 101,
            "status": "approved",
            "resolved_by": "Vic",
            "reason": None,
            "details": {"accepted_items": [0, 1]},
        },
    )
    assert done.status == "applied" and done.po_names == ["P00700", "P00701"]
    assert "2 RFQ(s) created (P00700, P00701)" in done.outcome.summary
    first, second = ports.created_rfqs
    assert first["partner_id"] == 42 and first["external_ref"] == "intreq-case_int1-42"
    assert first["lines"] == [
        {
            "product_id": 11,
            "product_qty": 20.0,
            "price_unit": 45.0,
            "date_planned": "2026-09-21T12:00:00+00:00",
        }
    ]
    assert second["partner_id"] == 10 and second["lines"][0]["product_id"] == 12
    assert first["origin"] == "internal request from ana.torres@empresa.com"
    # the request is on record for the status replies, linked to the first RFQ
    [request] = ports.internal_requests
    assert request.po_names == ["P00700", "P00701"] and request.requester_address == REQUESTER
    assert ports.links[-1]["po_id"] == 700 and ports.links[-1]["graph_message_id"] == MSG
    # the requester was answered in the same thread, without another approval
    draft = ports.drafts["reply1"]
    assert isinstance(draft, dict) and draft["reply_to"] == MSG
    assert "P00700, P00701" in draft["html_body"] and "banderines" in draft["html_body"]
    assert ports.sent_ids == ["reply1"] and len(approval_ports.created) == 1
    assert done.outbound is not None and done.outbound.kind == "ack"
    assert cleared(await agent.snapshot("case_int1"))


async def test_a_rejected_request_tells_the_requester_and_creates_nothing(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    chat.responses.append(_extraction())
    agent = make_agent()
    await agent.run(_task("case_int2"))
    done = await agent.resume(
        "case_int2",
        {
            "approval_id": 101,
            "status": "rejected",
            "resolved_by": "Vic",
            "reason": "no budget this month",
            "details": None,
        },
    )
    assert (
        done.status == "rejected"
        and "rejected by Vic (no budget this month)" in done.outcome.summary
    )
    assert ports.created_rfqs == [] and ports.internal_requests == []
    draft = ports.drafts["reply1"]
    assert isinstance(draft, dict) and "no budget this month" in draft["html_body"]


async def test_an_email_with_nothing_to_order_goes_to_a_person(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    ports.inbound[MSG] = "Gracias por la entrega de ayer, todo llegó bien."
    chat.responses.append(RequestExtraction(items=[], confidence=0.2))
    result = await make_agent().run(_task("case_int3"))
    assert result.status == "escalated" and result.outcome.approval_id == 101
    assert approval_ports.created[0]["kind"] == "escalation"
    assert ports.created_rfqs == [] and ports.drafts == {}


async def test_the_status_reply_answers_the_original_request(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    chat.responses.append(_extraction())
    agent = make_agent()
    await agent.run(_task("case_int4"))
    await agent.resume(
        "case_int4",
        {"approval_id": 101, "status": "approved", "resolved_by": "Vic", "details": None},
    )
    ports.contexts["P00700"] = demo_context(name="P00700")  # confirmed with the supplier
    told = await agent.run(
        SupplierCommsTask(
            kind="status_reply", case_id="pb_1_tell_confirmed", po_name="P00700", notes="confirmed"
        )
    )
    assert told.status == "sent" and told.outbound is not None and told.outbound.kind == "status"
    assert told.outbound.to == [REQUESTER]
    draft = ports.drafts["reply2"]
    assert isinstance(draft, dict) and draft["reply_to"] == MSG
    assert "P00700" in draft["html_body"] and "confirmed" in draft["html_body"]
    assert draft["headers"]["x-sc-po"] == "P00700"
    # the second milestone closes the request on record
    ports.contexts["P00700"] = demo_context(name="P00700").model_copy(
        update={
            "lines": [ln.model_copy(update={"qty_received": ln.qty}) for ln in demo_context().lines]
        }
    )
    arrived = await agent.run(
        SupplierCommsTask(kind="status_reply", case_id="pb_1_tell_received", po_name="P00700")
    )
    assert arrived.status == "sent" and "received" in arrived.outcome.summary
    assert ports.internal_requests[0].status == "done"
    # an order nobody requested internally is a failure, not a reply
    ports.contexts["P00015"] = demo_context()
    nobody = await agent.run(
        SupplierCommsTask(kind="status_reply", case_id="pb_9", po_name="P00015", notes="confirmed")
    )
    assert (
        nobody.status == "failed" and "no internal request behind P00015" in nobody.outcome.summary
    )
