"""send_po on a seeded confirmed order: Odoo renders the PDF and the supplier receives it."""

from __future__ import annotations

from typing import Any

import pytest

from sc_core.mail.graph import GraphMailClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import ApprovalRepo, PartnerRepo, PurchaseOrderRepo
from sc_core.schema.a2a import SupplierCommsTask

from .conftest import SUPPLIER_EMAIL

pytestmark = [pytest.mark.integration, pytest.mark.graph, pytest.mark.odoo, pytest.mark.llm]


async def test_confirmed_order_pdf_is_attached_and_sent(
    live_agent: dict[str, Any], odoo: OdooClient, odoo_admin: OdooClient, graph: GraphMailClient
) -> None:
    partner = await PartnerRepo(odoo).find_by_email(SUPPLIER_EMAIL)
    if partner is None:
        pytest.skip("demo supplier missing; run `just odoo-seed`")
    company = await PartnerRepo(odoo).commercial_partner(partner)
    confirmed = await PurchaseOrderRepo(odoo).find(
        [["partner_id", "=", company.id], ["state", "=", "purchase"]], limit=1, order="id desc"
    )
    if not confirmed:
        pytest.skip("no confirmed order for the demo supplier; run `just odoo-seed`")
    po = confirmed[0]

    pdf = await PurchaseOrderRepo(odoo).report_pdf(po.id)
    assert pdf.startswith(b"%PDF") and len(pdf) > 5_000

    agent = live_agent["agent"]
    case_id = f"e2e_po_{po.name.lower()}"
    paused = await agent.run(SupplierCommsTask(kind="send_po", case_id=case_id, po_name=po.name))
    assert paused.status == "awaiting_approval", paused.outcome.summary
    assert paused.outbound is not None and paused.outbound.attachments == [f"{po.name}.pdf"]
    attachments = await graph.attachments(paused.outbound.draft_id or "")
    assert [a.name for a in attachments] == [f"{po.name}.pdf"]
    assert attachments[0].data.startswith(b"%PDF")

    approval = await ApprovalRepo(odoo_admin).resolve(paused.outcome.approval_id or 0, "approved")
    sent = await agent.resume(case_id, {"approval_id": approval.id, "status": "approved"})
    assert sent.status == "sent", sent.outcome.summary
    link = sent.outbound.web_link if sent.outbound else ""
    print(f"\n>>> {po.name} sent with {po.name}.pdf to {SUPPLIER_EMAIL}: {link}")
