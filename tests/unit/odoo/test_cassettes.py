"""Repository behaviour on responses recorded from the local Odoo 18 with the seeded dataset.

These run offline from ``tests/fixtures/odoo/*.json``. They must use fixed
inputs (no ``now()``) so the recorded requests match on replay, and they find
records by the seed's natural keys (``sc_external_ref``, bill ``ref``, the
supplier's email) rather than by names or ids. Refresh the recordings with
``just odoo-record`` after ``just odoo-fresh`` or when the repositories change.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from director.desk.demo import OdooDemoWorld
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

SEED = 20260912
INCOMING = f"seed-open-{SEED}-2"  # Proveedor Hidraulica, confirmed, due in 12 days
RECEIVED = f"seed-open-{SEED}-6"  # Proveedor Hidraulica, received in full, draft bill F001-000201
HIDRAULICA = "Proveedor Hidraulica"
HIDRAULICA_EMAIL = "ventas.hidraulica.sc@gmail.com"


async def test_purchase_orders_from_seeded_data(cassette: Cassette) -> None:
    repo = PurchaseOrderRepo(cassette("purchase_orders"))
    po = await repo.find_by_external_ref(INCOMING)
    assert po is not None and po.partner_id.name == HIDRAULICA and po.state == "purchase"
    lines = await repo.lines(po.id)
    assert len(lines) == 3 and all(line.order_id.id == po.id for line in lines)
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
    hidraulica = next(s for s in suppliers if s.name == HIDRAULICA)

    prices = await SupplierInfoRepo(client).for_partner(hidraulica.id)
    assert len(prices) == 30 and all(p.partner_id.id == hidraulica.id for p in prices)

    rules = await OrderpointRepo(client).list()
    assert rules and rules[0].product_min_qty <= rules[0].product_max_qty
    by_product = await OrderpointRepo(client).for_product(rules[0].product_id.id)
    assert by_product is not None and by_product.id == rules[0].id


async def test_pickings_for_seeded_order(cassette: Cassette) -> None:
    client = cassette("pickings")
    po = await PurchaseOrderRepo(client).find_by_external_ref(INCOMING)
    assert po is not None
    pickings = await PickingRepo(client).incoming_for_po(po.id)
    assert pickings and pickings[0].purchase_id is not None and pickings[0].purchase_id.id == po.id
    moves = await PickingRepo(client).moves(pickings[0].id)
    assert moves and moves[0].picking_id is not None and moves[0].picking_id.id == pickings[0].id


async def test_received_lines_and_draft_bill_for_hidraulica(cassette: Cassette) -> None:
    """The received order carries the seed's draft bill; the repo finds it and never posts."""
    client = cassette("bills_and_move_lines")
    po = await PurchaseOrderRepo(client).find_by_external_ref(RECEIVED)
    assert po is not None and po.partner_id.name == HIDRAULICA

    received = await StockMoveLineRepo(client).received_for_po(po.id)
    assert len(received) == len(po.order_line) == 4
    assert all(line.state == "done" and line.picked for line in received)
    by_picking = await StockMoveLineRepo(client).for_picking(received[0].picking_id.id)  # type: ignore[union-attr]
    assert {line.id for line in by_picking} == {line.id for line in received}
    needle = next(line for line in received if "[NFCC-LCN]" in line.product_id.name)
    assert needle.quantity == 12.0  # four weeks of demand at 3 a week

    bills = AccountMoveRepo(client)
    draft = await bills.create_draft_bill(po.id, ref="F001-000201")
    assert draft.is_draft and draft.ref == "F001-000201"
    assert draft.partner_id is not None and draft.partner_id.name == HIDRAULICA
    again = await bills.create_draft_bill(po.id, ref="F001-000201")
    assert again.id == draft.id  # idempotent: one draft per order
    lines = await bills.lines(draft.id)
    assert len(lines) == 4 and all(line.purchase_line_id is not None for line in lines)
    assert sum(line.quantity for line in lines) == sum(line.quantity for line in received)
    assert (await bills.find_by_ref(po.partner_id.id, "F001-000201")) is not None
    assert [b.id for b in await bills.bills_for_po(po.id)] == [draft.id]
    assert draft.id in {b.id for b in await bills.bills_for_partner(po.partner_id.id)}


async def test_partner_email_lookups(cassette: Cassette) -> None:
    repo = PartnerRepo(cassette("partners"))
    hidraulica = await repo.find_by_email("Ventas.Hidraulica.SC@gmail.com")
    assert hidraulica is not None and hidraulica.name == HIDRAULICA
    same_domain = await repo.find_by_email_domain("gmail.com")
    assert any(p.id == hidraulica.id for p in same_domain)
    assert hidraulica.email_normalized in await repo.emails_of(hidraulica.id)
    assert await repo.find_by_email("nobody@nowhere.invalid") is None


async def test_sourcing_reads_the_valve_price_list_and_the_suppliers_emails(
    cassette: Cassette,
) -> None:
    """What a quote round on the demo valve starts from: three listed suppliers, one email."""
    client = cassette("sourcing_round")
    partners = PartnerRepo(client)
    hidraulica = await partners.find_by_email(HIDRAULICA_EMAIL)
    assert hidraulica is not None
    company = await partners.commercial_partner(hidraulica)
    assert company.name == HIDRAULICA and await partners.emails_of(company.id) == [HIDRAULICA_EMAIL]

    [valve] = await client.search_read(
        "product.product", [["default_code", "=", "CBEA-LHN"]], ["id"], limit=1
    )
    entries = await SupplierInfoRepo(client).for_product(int(valve["id"]))
    by_partner = {e.partner_id.name: e for e in entries}
    assert set(by_partner) == {HIDRAULICA, "Hidráulica Alterna SAC", "Importadora del Sur SAC"}
    assert by_partner[HIDRAULICA].price == 104.16 and by_partner[HIDRAULICA].delay == 30
    assert by_partner["Importadora del Sur SAC"].min_qty == 20.0
    # the other two have no address in Odoo: a round marks their RFQs "no email"
    for name in ("Hidráulica Alterna SAC", "Importadora del Sur SAC"):
        assert await partners.emails_of(by_partner[name].partner_id.id) == []


async def test_demo_world_finds_the_late_order_and_owns_a_receipt_order(
    cassette: Cassette,
) -> None:
    """What a demo reset reads and creates: the supplier's most overdue order, and a
    confirmed order of its own (idempotent by external reference) with lines to quote."""
    world = OdooDemoWorld(cassette("demo_world"), None, today=lambda: date(2026, 9, 14))
    late = await world.late_order(HIDRAULICA_EMAIL)
    assert late is not None and late.po_name.startswith("P") and late.date_planned is not None
    assert late.date_planned < date(2026, 9, 14)
    receipt = await world.create_confirmed_order(
        HIDRAULICA_EMAIL, external_ref="demo-cassette-receipt"
    )
    assert receipt.partner_id == late.partner_id and receipt.amount_total > 0
    lines = await world.order_lines(receipt.po_name)
    assert len(lines) == 2 and all(line.qty == 10 and line.price_unit > 0 for line in lines)
    assert not world.can_receive  # no warehouse login here: receipts and bills stay manual
