"""A receipt: reconciled in code; a discrepancy becomes an email a person approves."""

from __future__ import annotations

from typing import Any

from logistics.testing import FakeLogisticsPorts, demo_receipt
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import LogisticsTask
from supplier_comms.models import DraftOutput
from tests.unit.graph.toy import FakeApprovalPorts

DRAFT = DraftOutput(
    subject="Diferencias en la recepción WH/IN/00042",
    html_body="<p>Estimados, recibimos 1 bomba de 2.</p><table><tr><th>Producto</th></tr></table>",
)


async def test_short_receipt_becomes_an_email_after_approval(
    make_agent: Any,
    ports: FakeLogisticsPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    chat.responses.append(DRAFT)
    agent = make_agent()
    paused = await agent.run(
        LogisticsTask(kind="reconcile_receipt", case_id="c_rcv1", picking_id=42)
    )
    assert paused.status == "awaiting_approval" and paused.po_name == "P00015"
    assert paused.reconciliation is not None and not paused.reconciliation.ok
    [short] = paused.reconciliation.discrepancies
    assert short.kind == "short" and short.received == 1.0
    assert paused.outbound is not None and paused.outbound.kind == "discrepancy"
    assert paused.outbound.subject == "[P00015] Diferencias en la recepción WH/IN/00042"
    assert paused.outbound.draft_id == "draft1"
    [created] = approval_ports.created
    assert created["kind"] == "send_email" and created["po_id"] == 7
    assert created["summary"] == "Report receipt discrepancies to Proveedor Hidraulica on P00015"
    assert 'style="border' in created["payload"]["html_body"]  # tables keep their borders
    assert created["payload"]["reconciliation"]["discrepancies"][0]["kind"] == "short"
    # the model saw the numbers and the receipt, not an invented story
    prompt = chat.last_prompt_text()
    assert "expected 2 Unidades, received 1 Unidades (short)" in prompt and "WH/IN/00042" in prompt

    done = await agent.resume(
        "c_rcv1", {"approval_id": 101, "status": "approved", "resolved_by": "Ana"}
    )
    assert done.status == "sent" and ports.base.sent_ids == ["draft1"]
    assert done.outbound is not None and done.outbound.sent_message_id
    assert any(
        "reporte de diferencias" in n.lower() or "discrepancy" in n.lower()
        for _, n in ports.base.notes
    )
    assert ports.runs[paused.run_id]["status"] == "sent"


async def test_matching_receipt_needs_nobody(
    make_agent: Any,
    ports: FakeLogisticsPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    ports.receipts[43] = demo_receipt().model_copy(update={"picking_id": 43, "name": "WH/IN/00043"})
    done = await make_agent().run(
        LogisticsTask(kind="reconcile_receipt", case_id="c_rcv2", picking_id=43)
    )
    assert (
        done.status == "no_action" and "matches P00015: 2 line(s) in full" in done.outcome.summary
    )
    assert approval_ports.created == [] and chat.calls == []
    assert any("WH/IN/00043" in n for _, n in ports.base.notes)


async def test_a_person_reports_damage_even_when_the_count_matches(
    make_agent: Any, ports: FakeLogisticsPorts, chat: ScriptedChatClient
) -> None:
    ports.receipts[43] = demo_receipt().model_copy(update={"picking_id": 43, "name": "WH/IN/00043"})
    chat.responses.append(DRAFT)
    paused = await make_agent().run(
        LogisticsTask(
            kind="report_discrepancy",
            case_id="c_rcv3",
            picking_id=43,
            notes="one pump arrived with a cracked housing",
        )
    )
    assert paused.status == "awaiting_approval"
    assert "cracked housing" in chat.last_prompt_text()
    assert paused.reconciliation is not None and paused.reconciliation.ok


async def test_receipt_without_an_order_or_unknown_fails_plainly(
    make_agent: Any, ports: FakeLogisticsPorts
) -> None:
    missing = await make_agent().run(
        LogisticsTask(kind="reconcile_receipt", case_id="c_rcv4", picking_id=99)
    )
    assert missing.status == "failed" and "receipt 99 not found" in missing.outcome.summary
    ports.receipts[44] = demo_receipt().model_copy(
        update={"picking_id": 44, "name": "WH/IN/00044", "po_id": None, "po_name": None}
    )
    orphan = await make_agent().run(
        LogisticsTask(kind="reconcile_receipt", case_id="c_rcv5", picking_id=44)
    )
    assert (
        orphan.status == "failed"
        and "does not come from a purchase order" in orphan.outcome.summary
    )
