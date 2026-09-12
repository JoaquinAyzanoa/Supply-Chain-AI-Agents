"""Phase 5 end to end on the demo supplier.

Part A (automatic, real DeepSeek): ``send_rfq`` on the supplier's open RFQ
-> approval in Odoo -> approved through RPC -> resumed -> the email is sent
from the bot mailbox, ``mail_outbound`` and the outbound link exist, the run
log says ``sent``.

Part B (``SC_E2E_SUPPLIER=1``): a person replies from the Gmail account with
a delivery date -> the mail sync links the reply -> the director dispatches
``handle_inbound`` -> the agent proposes the new date -> approved through
RPC -> resumed -> ``date_planned`` of every line is the supplier's date.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any

import pytest

from director.dispatch import Dispatcher, MemoryEventResults
from sc_core.a2a import AgentReply
from sc_core.mail.graph import GraphMailClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import AgentRunRepo, ApprovalRepo, MailLinkRepo, PurchaseOrderRepo
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.events import InboundMailLinked
from supplier_comms.handler import SupplierCommsHandler

from .conftest import SUPPLIER_EMAIL

pytestmark = [pytest.mark.integration, pytest.mark.graph, pytest.mark.odoo, pytest.mark.llm]

EXPECTED_DATE = date(2026, 10, 20)


async def _open_rfq(odoo: OdooClient) -> Any:
    from sc_core.odoo.repositories import PartnerRepo

    partner = await PartnerRepo(odoo).find_by_email(SUPPLIER_EMAIL)
    if partner is None:
        pytest.skip("demo supplier missing; run `just odoo-demo-supplier`")
    company = await PartnerRepo(odoo).commercial_partner(partner)
    orders = await PurchaseOrderRepo(odoo).open_for_partner(company.id)
    if not orders:
        pytest.skip("no open order for the demo supplier; run `just odoo-demo-supplier`")
    return orders[0]


async def _approve(odoo_admin: OdooClient, approval_id: int) -> str:
    approval = await ApprovalRepo(odoo_admin).resolve(approval_id, "approved")
    assert approval.status == "approved"
    return approval.resolved_by_id.name if approval.resolved_by_id else "admin"


async def test_rfq_sent_after_approval_then_reply_applied(
    live_agent: dict[str, Any], odoo: OdooClient, odoo_admin: OdooClient, graph: GraphMailClient
) -> None:
    agent = live_agent["agent"]
    po = await _open_rfq(odoo)
    case_id = f"e2e_{po.name.lower()}"

    # --- part A: draft, approve, send -----------------------------------------------
    paused = await agent.run(SupplierCommsTask(kind="send_rfq", case_id=case_id, po_name=po.name))
    assert paused.status == "awaiting_approval", paused.outcome.summary
    assert paused.outbound is not None and paused.outbound.draft_id
    assert paused.outbound.subject.startswith(f"[{po.name}]")
    assert paused.outbound.to == [SUPPLIER_EMAIL]
    approval = await ApprovalRepo(odoo).get(paused.outcome.approval_id or 0)
    assert approval.status == "pending" and approval.kind == "send_email"
    payload = ApprovalRepo.payload_of(approval)
    assert payload["draft_id"] == paused.outbound.draft_id and "<" in payload["html_body"]
    draft = await graph.get_message(paused.outbound.draft_id)
    assert draft.subject == paused.outbound.subject

    who = await _approve(odoo_admin, approval.id)
    sent = await agent.resume(
        case_id, {"approval_id": approval.id, "status": "approved", "resolved_by": who}
    )
    assert sent.status == "sent", sent.outcome.summary
    assert sent.outbound is not None and sent.outbound.sent_message_id
    links = await MailLinkRepo(odoo).for_po(po.id)
    assert any(link.direction == "out" and link.case_id == case_id for link in links)
    run = await AgentRunRepo(odoo).get_by_run_id(sent.run_id)
    assert run is not None and run.status == "sent" and run.po_id and run.po_id.id == po.id
    print(f"\n>>> RFQ {po.name} sent to {SUPPLIER_EMAIL}: {sent.outbound.web_link}", flush=True)

    # --- part B: the supplier replies, the sync links it, the director dispatches -----
    if os.environ.get("SC_E2E_SUPPLIER") != "1":
        print(">>> Part B skipped (set SC_E2E_SUPPLIER=1 and reply from Gmail).", flush=True)
        return
    wait = float(os.environ.get("SC_E2E_WAIT_SECONDS", "600"))
    print(
        f">>> Reply from {SUPPLIER_EMAIL} to that email, keeping the subject, with the text: "
        f"'Confirmamos entrega el 20 de octubre de 2026'. Waiting up to {wait:.0f}s.",
        flush=True,
    )
    reply_id = await _wait_for_reply(graph, sent.outbound.sent_message_id, wait)
    event = InboundMailLinked(
        source="mail_sync",
        case_id=f"{case_id}_reply",
        po_id=po.id,
        po_name=po.name,
        graph_message_id=reply_id,
        confidence="exact",
        rule="conversation",
    )

    class LocalCaller:  # the director's A2A call, in-process
        async def send(self, task_json: str, *, case_id: str, metadata: Any = None) -> AgentReply:
            return await SupplierCommsHandler(_Provider(agent)).handle(
                task_json, {"case_id": case_id}
            )

    results = MemoryEventResults()
    result = await Dispatcher(LocalCaller(), results).dispatch(event)
    assert result is not None and result["status"] == "input_required", result
    reply = result["reply"]
    assert reply["classification"]["kind"] == "eta_update", reply["classification"]
    assert reply["extracted"]["eta_date"] == EXPECTED_DATE.isoformat(), reply["extracted"]
    approval_id = reply["outcome"]["approval_id"]
    who = await _approve(odoo_admin, approval_id)
    applied = await agent.resume(
        event.case_id, {"approval_id": approval_id, "status": "approved", "resolved_by": who}
    )
    assert applied.status == "applied", applied.outcome.summary
    lines = await PurchaseOrderRepo(odoo).lines(po.id)
    assert all(line.date_planned and line.date_planned.date() == EXPECTED_DATE for line in lines)
    fresh = await PurchaseOrderRepo(odoo).get(po.id)
    assert fresh.sc_eta_source == "supplier"
    print(f">>> {po.name}: date_planned = {EXPECTED_DATE} on {len(lines)} line(s)", flush=True)


class _Provider:
    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def get(self) -> Any:
        return self.agent


async def _wait_for_reply(graph: GraphMailClient, sent_message_id: str, timeout: float) -> str:
    sent = await graph.get_message(sent_message_id)
    baseline = await graph.inbox_delta(None)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        page = await graph.inbox_delta(baseline.delta_link)
        baseline = page
        for message in page.messages:
            same_thread = message.conversation_id == sent.conversation_id
            from_supplier = (
                message.sender is not None and message.sender.normalized == SUPPLIER_EMAIL
            )
            if same_thread and from_supplier:
                return message.id
        await asyncio.sleep(5)
    pytest.fail(f"no reply from {SUPPLIER_EMAIL} within {timeout:.0f}s")
