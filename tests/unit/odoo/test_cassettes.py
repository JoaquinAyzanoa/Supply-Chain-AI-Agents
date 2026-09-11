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
    OrderpointRepo,
    PartnerRepo,
    PickingRepo,
    PurchaseOrderRepo,
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


async def test_partner_email_lookups(cassette: Cassette) -> None:
    repo = PartnerRepo(cassette("partners"))
    azure = await repo.find_by_email("azure.Interior24@example.com")
    assert azure is not None and azure.name == "Azure Interior"
    same_domain = await repo.find_by_email_domain("example.com")
    assert any(p.id == azure.id for p in same_domain)
    assert azure.email_normalized in await repo.emails_of(azure.id)
    assert await repo.find_by_email("nobody@nowhere.invalid") is None
