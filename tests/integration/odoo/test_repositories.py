"""Repositories against the real Odoo container (demo data)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from injector import Injector

from sc_core.infra.health import HealthRegistry, OverallStatus
from sc_core.infra.module import CoreModule, OdooModule
from sc_core.infra.settings import Settings
from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import NewOrderLine
from sc_core.odoo.repositories import (
    ActivityRepo,
    AgentRunRepo,
    ApprovalRepo,
    MailLinkRepo,
    OrderpointRepo,
    PartnerRepo,
    PickingRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
)

pytestmark = [pytest.mark.integration, pytest.mark.odoo]


async def _demo_supplier_and_product(client: OdooClient) -> tuple[int, int]:
    po = (
        await client.search_read(
            "purchase.order", [["state", "=", "purchase"]], ["partner_id", "order_line"], limit=1
        )
    )[0]
    line = (await client.read("purchase.order.line", po["order_line"][:1], ["product_id"]))[0]
    return po["partner_id"][0], line["product_id"][0]


async def test_rfq_roundtrip(odoo_client: OdooClient) -> None:
    repo = PurchaseOrderRepo(odoo_client)
    partner_id, product_id = await _demo_supplier_and_product(odoo_client)
    ref = f"test:{uuid4().hex[:8]}"
    lines = [
        NewOrderLine(product_id=product_id, product_qty=3),
        NewOrderLine(product_id=product_id, product_qty=1),
    ]

    po = await repo.create_rfq(partner_id, lines, external_ref=ref, origin="integration test")
    try:
        assert po.is_rfq and po.sc_external_ref == ref and po.origin == "integration test"
        assert len(await repo.lines(po.id)) == 2
        again = await repo.create_rfq(partner_id, lines, external_ref=ref)
        assert again.id == po.id  # idempotent

        line = (await repo.lines(po.id))[0]
        when = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
        await repo.set_line_date_planned(line.id, when, source="supplier", run_id="run_int")
        assert (await repo.line(line.id)).date_planned == when
        await repo.set_eta_meta(po.id, source="supplier", confidence=0.8)
        assert (await repo.get(po.id)).sc_eta_confidence == 0.8

        note_id = await repo.post_note(po.id, "<p>integration note</p>")
        assert note_id > 0
        messages = await odoo_client.search_read(
            "mail.message",
            [["model", "=", "purchase.order"], ["res_id", "=", po.id]],
            ["body"],
            limit=5,
            order="id desc",
        )
        assert any("run_int" in m["body"] for m in messages)
    finally:
        await repo.cancel(po.id)


async def test_open_orders_queries(odoo_client: OdooClient) -> None:
    repo = PurchaseOrderRepo(odoo_client)
    partner_id, _ = await _demo_supplier_and_product(odoo_client)
    assert await repo.open_for_partner(partner_id)
    late = await repo.late_open_orders(date.today() + timedelta(days=365))
    assert all(po.is_open for po in late)


async def test_supplierinfo_and_products(
    odoo_client: OdooClient, odoo_admin_client: OdooClient
) -> None:
    repo = SupplierInfoRepo(odoo_client)
    row = (
        await odoo_client.search_read(
            "product.supplierinfo", [], ["partner_id", "product_tmpl_id"], limit=1
        )
    )[0]
    variant = (
        await odoo_client.search_read(
            "product.product",
            [["product_tmpl_id", "=", row["product_tmpl_id"][0]]],
            ["id"],
            limit=1,
        )
    )[0]
    entries = await repo.for_product(variant["id"])
    assert entries and all(
        e.product_tmpl_id and e.product_tmpl_id.id == row["product_tmpl_id"][0] for e in entries
    )

    updated = await repo.upsert_price(
        partner_id=row["partner_id"][0],
        product_tmpl_id=row["product_tmpl_id"][0],
        price=123.45,
        currency_id=1,
        min_qty=999.0,
        delay=4,
    )
    assert updated.price == 123.45 and updated.min_qty == 999.0 and updated.delay == 4
    same = await repo.upsert_price(
        partner_id=row["partner_id"][0],
        product_tmpl_id=row["product_tmpl_id"][0],
        price=100.0,
        currency_id=1,
        min_qty=999.0,
    )
    assert same.id == updated.id and same.price == 100.0
    # the bot has no unlink rights on price lists by design; clean up as admin
    await odoo_admin_client.unlink("product.supplierinfo", [updated.id])


async def test_orderpoints(odoo_client: OdooClient, odoo_admin_client: OdooClient) -> None:
    repo = OrderpointRepo(odoo_client)
    existing = await repo.list()
    assert existing
    op = await repo.set_min_max(
        existing[0].id, minimum=existing[0].product_min_qty, maximum=existing[0].product_max_qty
    )
    assert op.id == existing[0].id
    wh = (await odoo_client.search_read("stock.warehouse", [], ["lot_stock_id"], limit=1))[0]
    # one rule per product and location: pick a purchasable product without one
    taken = [op.product_id.id for op in existing]
    product = (
        await odoo_client.search_read(
            "product.product",
            [["type", "=", "consu"], ["purchase_ok", "=", True], ["id", "not in", taken]],
            ["id"],
            limit=1,
        )
    )[0]
    created = await repo.create(
        product_id=product["id"],
        warehouse_id=wh["id"],
        location_id=wh["lot_stock_id"][0],
        minimum=1,
        maximum=2,
    )
    try:
        assert created.product_min_qty == 1.0 and created.trigger == "auto"
    finally:
        await odoo_admin_client.unlink("stock.warehouse.orderpoint", [created.id])


async def test_pickings_and_moves(odoo_client: OdooClient) -> None:
    repo = PickingRepo(odoo_client)
    po_row = (
        await odoo_client.search_read(
            "purchase.order", [["picking_ids", "!=", False]], ["id"], limit=1
        )
    )[0]
    pickings = await repo.incoming_for_po(po_row["id"])
    assert pickings and pickings[0].picking_type_code == "incoming"
    moves = await repo.moves(pickings[0].id)
    assert moves and moves[0].product_uom_qty > 0
    original = pickings[0].scheduled_date
    assert original is not None
    updated = await repo.set_scheduled_date(
        pickings[0].id, original + timedelta(days=1), source="tracking", run_id="run_int"
    )
    assert updated.sc_eta_source == "tracking"
    await repo.set_scheduled_date(pickings[0].id, original, source="supplier", run_id="run_int")


async def test_partners(odoo_client: OdooClient) -> None:
    repo = PartnerRepo(odoo_client)
    suppliers = await repo.suppliers()
    assert suppliers and all(s.is_company for s in suppliers)
    any_with_email = next(p for p in await repo.find([["email_normalized", "!=", False]], limit=5))
    assert any_with_email.email_normalized
    found = await repo.find_by_email(any_with_email.email_normalized.upper())
    assert found is not None and found.id == any_with_email.id
    domain = any_with_email.email_domain
    assert domain and any(
        p.id == any_with_email.id for p in await repo.find_by_email_domain(domain)
    )
    company = await repo.commercial_partner(any_with_email)
    assert company.is_company or company.id == any_with_email.id
    assert (
        any_with_email.email_normalized in await repo.emails_of(company.id)
        or company.id != any_with_email.id
    )


async def test_activities(odoo_client: OdooClient) -> None:
    repo = ActivityRepo(odoo_client)
    po_row = (await odoo_client.search_read("purchase.order", [], ["id"], limit=1))[0]
    activity = await repo.create_approval(
        res_model="purchase.order",
        res_id=po_row["id"],
        user_id=await odoo_client.uid(),
        summary="Integration approval",
        note_html="<p>please review</p>",
        deadline=date.today(),
    )
    assert activity.summary == "Integration approval" and activity.res_id == po_row["id"]
    assert any(a.id == activity.id for a in await repo.pending_for("purchase.order", po_row["id"]))
    await repo.mark_done(activity.id, feedback="done by test")
    assert all(a.id != activity.id for a in await repo.pending_for("purchase.order", po_row["id"]))


async def test_agent_runs_approvals_and_mail_links(odoo_client: OdooClient) -> None:
    runs, approvals, links = (
        AgentRunRepo(odoo_client),
        ApprovalRepo(odoo_client),
        MailLinkRepo(odoo_client),
    )
    po_row = (
        await odoo_client.search_read(
            "purchase.order", [["state", "=", "purchase"]], ["id"], limit=1
        )
    )[0]
    run_id = f"run_{uuid4().hex[:8]}"
    run = await runs.start(
        run_id=run_id,
        agent="supplier_comms",
        case_id="case_int",
        po_id=po_row["id"],
        model="deepseek-v4-flash",
    )
    assert (await runs.start(run_id=run_id, agent="x", case_id="y")).id == run.id
    assert await runs.finish(run_id, "no_action", "nothing to do") is True
    assert await runs.finish("does-not-exist", "failed") is False
    assert (await runs.get_by_run_id(run_id)).status == "no_action"  # type: ignore[union-attr]

    approval = await approvals.create(
        kind="po_change",
        summary="integration",
        payload={"changes": []},
        requested_by="supplier_comms",
        case_id="case_int",
        po_id=po_row["id"],
        run_id=run_id,
    )
    assert approval.is_pending and approval.po_id and approval.po_id.id == po_row["id"]
    assert any(a.id == approval.id for a in await approvals.pending_for_po(po_row["id"]))
    resolved = await approvals.resolve(approval.id, "rejected", reason="test cleanup")
    assert resolved.status == "rejected" and resolved.reason == "test cleanup"
    assert ApprovalRepo.payload_of(resolved) == {"changes": []}

    msg_id = f"AAMk-int-{uuid4().hex[:8]}"
    link = await links.link(
        po_id=po_row["id"],
        graph_message_id=msg_id,
        direction="in",
        conversation_id="conv-int",
        received_at=datetime.now(UTC),
        web_link="https://outlook.live.com/x",
        confidence="exact",
    )
    assert (
        await links.link(po_id=po_row["id"], graph_message_id=msg_id, direction="in")
    ).id == link.id
    assert (await links.find_by_conversation("conv-int")) is not None
    assert any(item.id == link.id for item in await links.for_po(po_row["id"]))


async def test_injector_module_wires_client_repos_and_health(odoo_settings: Settings) -> None:
    health = HealthRegistry()
    injector = Injector([CoreModule(odoo_settings, health), OdooModule()])
    repo = injector.get(PurchaseOrderRepo)
    assert injector.get(OdooClient) is injector.get(OdooClient)
    assert await repo.count([]) > 0
    assert "odoo" in health.names()
    report = await health.run(service="t", version="1")
    assert report.status is OverallStatus.OK
    await injector.get(OdooClient).aclose()
