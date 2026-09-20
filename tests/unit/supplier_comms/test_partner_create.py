"""An unknown sender who quotes something we buy becomes a supplier only after approval."""

from __future__ import annotations

from typing import Any

from sc_core.graph import cleared
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import QuotationData, QuotedLine, SupplierCommsTask
from supplier_comms.domain.models import InboundMeta
from supplier_comms.graph.nodes.partner import suggested_name
from supplier_comms.testing import FakePorts
from tests.unit.graph.toy import FakeApprovalPorts

MSG = "AAMk-new-supplier-1"
SENDER = "ventas@aceros-lima.com"


def _task(case_id: str) -> SupplierCommsTask:
    return SupplierCommsTask(kind="resolve_unlinked", case_id=case_id, graph_message_id=MSG)


def _prepare(ports: FakePorts) -> None:
    ports.inbound[MSG] = (
        "Buenos días, les cotizamos: CBEA-LHN válvula de contrabalance, 10 unidades a USD 98.50 "
        "c/u, entrega 20 días. También mangueras ref MG-99 a USD 12."
    )
    ports.metas[MSG] = InboundMeta(
        graph_message_id=MSG, sender_address=SENDER, web_link="https://outlook/new"
    )
    ports.products_by_code["CBEA-LHN"] = (1, "[CBEA-LHN] Válvula de contrabalance")


def _quotation() -> QuotationData:
    return QuotationData(
        lines=[
            QuotedLine(
                product_ref="CBEA-LHN",
                description="Válvula de contrabalance",
                qty=10,
                unit_price=98.5,
                currency="USD",
                lead_days=20,
            ),
            QuotedLine(
                product_ref="MG-99", description="Manguera", qty=1, unit_price=12.0, currency="USD"
            ),
        ],
        currency="USD",
        confidence=0.9,
    )


def test_the_suggested_name_comes_from_the_domain() -> None:
    assert suggested_name("ventas@aceros-lima.com") == "Aceros Lima"
    assert suggested_name("juan.perez@gmail.com") == "Juan Perez"
    assert suggested_name(None) == "New supplier"


async def test_a_quoting_stranger_pauses_on_partner_create_and_is_created_on_approval(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    chat.responses.append(_quotation())
    agent = make_agent()
    result = await agent.run(_task("case_new1"))
    assert result.status == "awaiting_approval" and result.outcome.approval_id == 101
    created = approval_ports.created[0]
    assert created["kind"] == "partner_create" and created["requested_by"] == "supplier_comms"
    payload = created["payload"]
    assert payload["sender_address"] == SENDER and payload["suggested_name"] == "Aceros Lima"
    assert [ln["product_ref"] for ln in payload["lines"]] == ["CBEA-LHN", "MG-99"]
    assert payload["facts"]["first_time_supplier"] is True
    assert "quoted 2 line(s)" in created["summary"]
    assert ports.created_partners == [] and ports.created_rfqs == []
    # the prompt saw the sender and the text, never a PO context
    assert SENDER in chat.last_prompt_text() and "CBEA-LHN" in chat.last_prompt_text()

    # approved with a corrected name: the partner, an RFQ for the matched line, the link
    done = await agent.resume(
        "case_new1",
        {
            "approval_id": 101,
            "status": "approved",
            "resolved_by": "Ana",
            "reason": None,
            "details": {"name": "Aceros Lima SAC"},
        },
    )
    assert done.status == "applied"
    assert "supplier Aceros Lima SAC created" in done.outcome.summary
    assert "RFQ P00700 with 1 quoted line(s)" in done.outcome.summary
    assert "not matched: MG-99" in done.outcome.summary
    assert ports.created_partners == [{"id": 500, "name": "Aceros Lima SAC", "email": SENDER}]
    [rfq] = ports.created_rfqs
    assert rfq["partner_id"] == 500 and rfq["external_ref"] == "newsupplier-case_new1"
    assert rfq["lines"] == [{"product_id": 1, "product_qty": 10.0, "price_unit": 98.5}]
    assert ports.links[-1] == {
        "po_id": 700,
        "graph_message_id": MSG,
        "direction": "in",
        "case_id": "case_new1",
        "confidence": "agent",
    }
    assert ports.notes and ports.notes[-1][0] == 700
    assert cleared(await agent.snapshot("case_new1"))


async def test_rejecting_creates_nothing(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    chat.responses.append(_quotation())
    agent = make_agent()
    await agent.run(_task("case_new2"))
    done = await agent.resume(
        "case_new2",
        {
            "approval_id": 101,
            "status": "rejected",
            "resolved_by": "Ana",
            "reason": "spam",
            "details": None,
        },
    )
    assert done.status == "rejected" and "rejected by Ana (spam)" in done.outcome.summary
    assert ports.created_partners == [] and ports.created_rfqs == [] and ports.links == []


async def test_a_known_sender_never_becomes_a_new_partner(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    ports.partners[SENDER] = 42  # already a partner: the old unlinked_mail path applies
    result = await make_agent().run(_task("case_new3"))
    assert result.status == "escalated" and approval_ports.created[0]["kind"] == "unlinked_mail"
    assert chat.calls == []
