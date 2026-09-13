"""Repository behaviour on responses recorded from the real Odoo 18 demo database.

These run offline from ``tests/fixtures/odoo/*.json``. They must use fixed
inputs (no ``now()``) so the recorded requests match on replay. Refresh the
recordings with ``just odoo-record`` when the repositories or the demo data
change.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import (
    AccountMoveRepo,
    OrderpointRepo,
    PartnerRepo,
    PickingRepo,
    PurchaseOrderRepo,
    StockMoveLineRepo,
    SupplierInfoRepo,
)

Cassette = Callable[[str], OdooClient]


async def test_purchase_orders_from_demo_data(cassette: Cassette) -> None:
    repo = PurchaseOrderRepo(cassette("purchase_orders"))
    po = await repo.get_by_name("P00015")
    assert po is not None and po.partner_id.name == "Ready Mat" and po.state == "purchase"
    lines = await repo.lines(po.id)
    assert len(lines) == 2 and all(line.order_id.id == po.id for line in lines)
    assert lines[0].product_id is not None and lines[0].price_unit > 0

    open_orders = await repo.open_for_partner(po.partner_id.id)
    assert any(o.id == po.id for o in open_orders)
    late = await repo.late_open_orders(date(2027, 1, 1))
    assert po.id in {o.id for o in late}
    assert await repo.get_by_name("DOES-NOT-EXIST") is None


async def test_suppliers_prices_and_rules(cassette: Cassette) -> None:
    client = cassette("suppliers_prices_rules")
    suppliers = await PartnerRepo(client).suppliers()
    assert suppliers and all(s.is_company and s.supplier_rank > 0 for s in suppliers)

    prices = await SupplierInfoRepo(client).for_partner(suppliers[0].id)
    assert all(p.partner_id.id == suppliers[0].id for p in prices)

    rules = await OrderpointRepo(client).list()
    assert rules and rules[0].product_min_qty <= rules[0].product_max_qty
    by_product = await OrderpointRepo(client).for_product(rules[0].product_id.id)
    assert by_product is not None and by_product.id == rules[0].id


async def test_pickings_for_demo_order(cassette: Cassette) -> None:
    client = cassette("pickings")
    po = await PurchaseOrderRepo(client).get_by_name("P00015")
    assert po is not None
    pickings = await PickingRepo(client).incoming_for_po(po.id)
    assert pickings and pickings[0].purchase_id is not None and pickings[0].purchase_id.id == po.id
    moves = await PickingRepo(client).moves(pickings[0].id)
    assert moves and moves[0].picking_id is not None and moves[0].picking_id.id == pickings[0].id


async def test_received_lines_and_draft_bill_for_hidraulica(cassette: Cassette) -> None:
    """P00016 (Proveedor Hidraulica) was received in full and is still to invoice."""
    client = cassette("bills_and_move_lines")
    po = await PurchaseOrderRepo(client).get_by_name("P00016")
    assert po is not None and po.partner_id.name == "Proveedor Hidraulica"

    received = await StockMoveLineRepo(client).received_for_po(po.id)
    assert len(received) == len(po.order_line) == 14
    assert all(line.state == "done" and line.picked for line in received)
    by_picking = await StockMoveLineRepo(client).for_picking(received[0].picking_id.id)  # type: ignore[union-attr]
    assert {line.id for line in by_picking} == {line.id for line in received}
    valve = next(line for line in received if "[CBEA-LHN]" in line.product_id.name)
    assert valve.quantity == 13.0

    bills = AccountMoveRepo(client)
    draft = await bills.create_draft_bill(po.id, ref="F001-000123", invoice_date=date(2024, 9, 2))
    assert draft.is_draft and draft.ref == "F001-000123" and draft.invoice_date == date(2024, 9, 2)
    assert draft.partner_id is not None and draft.partner_id.name == "Proveedor Hidraulica"
    again = await bills.create_draft_bill(po.id, ref="F001-000123", invoice_date=date(2024, 9, 2))
    assert again.id == draft.id  # idempotent: one draft per order
    lines = await bills.lines(draft.id)
    assert len(lines) == 14 and all(line.purchase_line_id is not None for line in lines)
    assert sum(line.quantity for line in lines) == sum(line.quantity for line in received)
    assert (await bills.find_by_ref(po.partner_id.id, "F001-000123")) is not None
    assert [b.id for b in await bills.bills_for_po(po.id)] == [draft.id]
    assert draft.id in {b.id for b in await bills.bills_for_partner(po.partner_id.id)}


async def test_partner_email_lookups(cassette: Cassette) -> None:
    repo = PartnerRepo(cassette("partners"))
    azure = await repo.find_by_email("azure.Interior24@example.com")
    assert azure is not None and azure.name == "Azure Interior"
    same_domain = await repo.find_by_email_domain("example.com")
    assert any(p.id == azure.id for p in same_domain)
    assert azure.email_normalized in await repo.emails_of(azure.id)
    assert await repo.find_by_email("nobody@nowhere.invalid") is None
