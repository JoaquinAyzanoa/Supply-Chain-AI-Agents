"""Planning repositories on a scripted transport: domains asked and aggregation done."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import (
    DemandRepo,
    IncomingRepo,
    ProductRepo,
    QuantRepo,
    WarehouseRepo,
    supplier_terms,
)

from .conftest import LOGIN_OK, ScriptedOdoo, rpc_ok

Factory = Callable[..., OdooClient]

PRODUCT = [49, "[CBEA-LHN] Válvula de contrabalance 3:1"]
CHECK = [56, "[CXDA-XCN] Válvula check"]


async def test_daily_sales_joins_lines_to_order_days(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    lines = [
        {
            "id": 1,
            "product_id": PRODUCT,
            "product_uom_qty": 3.0,
            "qty_delivered": 3.0,
            "order_id": [10, "S00010"],
        },
        {
            "id": 2,
            "product_id": PRODUCT,
            "product_uom_qty": 2.0,
            "qty_delivered": 1.0,
            "order_id": [11, "S00011"],
        },
        {
            "id": 3,
            "product_id": CHECK,
            "product_uom_qty": 5.0,
            "qty_delivered": 5.0,
            "order_id": [10, "S00010"],
        },
        {
            "id": 4,
            "product_id": PRODUCT,
            "product_uom_qty": 4.0,
            "qty_delivered": 4.0,
            "order_id": [12, "S00012"],
        },
    ]
    orders = [
        {"id": 10, "date_order": "2026-08-03 14:00:00"},
        {"id": 11, "date_order": "2026-08-03 18:30:00"},
        {"id": 12, "date_order": "2026-08-10 09:00:00"},
    ]
    odoo.script += [LOGIN_OK, rpc_ok(lines), rpc_ok(orders)]
    rows = await DemandRepo(client_factory()).daily_sales([49, 56], since=date(2026, 8, 1))
    assert [(r.product_id, r.day.isoformat(), r.ordered, r.delivered) for r in rows] == [
        (49, "2026-08-03", 5.0, 4.0),
        (56, "2026-08-03", 5.0, 5.0),
        (49, "2026-08-10", 4.0, 4.0),
    ]
    model, method, args, kwargs = odoo.execute_kw_args(-2)
    assert (model, method) == ("sale.order.line", "search_read")
    assert ["state", "in", ["sale", "done"]] in args[0]
    assert ["order_id.date_order", ">=", "2026-08-01 00:00:00"] in args[0]
    assert kwargs["order"] == "id asc" and kwargs["limit"] == 500


async def test_daily_sales_without_products_asks_nothing(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    assert await DemandRepo(client_factory()).daily_sales([], since=date(2026, 8, 1)) == []
    assert odoo.calls() == []


async def test_daily_shipped_buckets_customer_moves(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    moves = [
        {"id": 1, "product_id": PRODUCT, "quantity": 3.0, "date": "2026-08-03 15:00:00"},
        {"id": 2, "product_id": PRODUCT, "quantity": 1.0, "date": "2026-08-03 16:00:00"},
    ]
    odoo.script += [LOGIN_OK, rpc_ok(moves)]
    rows = await DemandRepo(client_factory()).daily_shipped([49], since=date(2026, 8, 1))
    assert len(rows) == 1 and rows[0].ordered == 4.0 and rows[0].day == date(2026, 8, 3)
    _, _, args, _ = odoo.execute_kw_args()
    assert ["location_dest_id.usage", "=", "customer"] in args[0]


async def test_on_hand_sums_quants_per_product(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    quants = [
        {
            "id": 1,
            "product_id": PRODUCT,
            "location_id": [8, "WH/Stock"],
            "warehouse_id": [1, "WH"],
            "quantity": 20.0,
            "reserved_quantity": 2.0,
        },
        {
            "id": 2,
            "product_id": PRODUCT,
            "location_id": [9, "WH/Stock/Shelf 1"],
            "warehouse_id": [1, "WH"],
            "quantity": 5.0,
            "reserved_quantity": 0.0,
        },
    ]
    odoo.script += [LOGIN_OK, rpc_ok(quants)]
    rows = await QuantRepo(client_factory()).on_hand([49], warehouse_id=1)
    assert len(rows) == 1 and rows[0].quantity == 25.0 and rows[0].reserved == 2.0
    assert rows[0].free == 23.0
    _, _, args, _ = odoo.execute_kw_args()
    assert ["location_id.usage", "=", "internal"] in args[0] and ["warehouse_id", "=", 1] in args[0]


async def test_open_supply_keeps_lines_with_something_to_receive(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    lines = [
        {
            "id": 441,
            "product_id": PRODUCT,
            "product_qty": 10.0,
            "qty_received": 4.0,
            "date_planned": "2026-09-20 12:00:00",
            "order_id": [66, "P00066"],
            "partner_id": [20, "Proveedor"],
        },
        {
            "id": 442,
            "product_id": PRODUCT,
            "product_qty": 5.0,
            "qty_received": 5.0,
            "date_planned": "2026-09-01 12:00:00",
            "order_id": [65, "P00065"],
            "partner_id": [20, "Proveedor"],
        },
        {
            "id": 443,
            "product_id": CHECK,
            "product_qty": 2.0,
            "qty_received": 0.0,
            "date_planned": False,
            "order_id": [67, "P00067"],
            "partner_id": False,
        },
    ]
    odoo.script += [LOGIN_OK, rpc_ok(lines)]
    rows = await IncomingRepo(client_factory()).open_supply([49, 56], warehouse_id=1)
    assert [(r.po_name, r.quantity, r.date_planned, r.partner_id) for r in rows] == [
        ("P00066", 6.0, date(2026, 9, 20), 20),
        ("P00067", 2.0, None, None),
    ]
    _, _, args, kwargs = odoo.execute_kw_args()
    assert ["order_id.picking_type_id.warehouse_id", "=", 1] in args[0]
    assert kwargs["order"] == "date_planned asc, id asc"


async def test_supplier_terms_map_template_entries_to_variants(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    products = [
        {"id": 49, "product_tmpl_id": [149, "CBEA-LHN"], "default_code": "CBEA-LHN"},
        {"id": 56, "product_tmpl_id": [156, "CXDA-XCN"], "default_code": "CXDA-XCN"},
    ]
    infos = [
        {
            "id": 1,
            "partner_id": [20, "Proveedor Hidraulica"],
            "product_tmpl_id": [149, "CBEA-LHN"],
            "product_id": False,
            "min_qty": 1.0,
            "price": 104.16,
            "currency_id": [1, "PEN"],
            "delay": 30,
            "sequence": 1,
        },
        {
            "id": 2,
            "partner_id": [21, "Alterno"],
            "product_tmpl_id": [149, "CBEA-LHN"],
            "product_id": [49, "CBEA-LHN"],
            "min_qty": 1.0,
            "price": 114.24,
            "currency_id": [1, "PEN"],
            "delay": 18,
            "sequence": 2,
        },
        {
            "id": 3,
            "partner_id": [20, "Proveedor Hidraulica"],
            "product_tmpl_id": [156, "CXDA-XCN"],
            "product_id": False,
            "min_qty": 5.0,
            "price": 27.26,
            "currency_id": [1, "PEN"],
            "delay": 25,
            "sequence": 1,
        },
    ]
    odoo.script += [LOGIN_OK, rpc_ok(products), rpc_ok(infos)]
    terms = await supplier_terms(client_factory(), [49, 56])
    assert [(t.product_id, t.partner_id, t.delay_days, t.min_qty, t.currency) for t in terms] == [
        (49, 20, 30, 1.0, "PEN"),
        (49, 21, 18, 1.0, "PEN"),
        (56, 20, 25, 5.0, "PEN"),
    ]


async def test_plannable_products_domain(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok([])]
    assert await ProductRepo(client_factory()).plannable(category="Hidráulica") == []
    _, _, args, kwargs = odoo.execute_kw_args()
    assert ["is_storable", "=", True] in args[0] and ["seller_ids", "!=", False] in args[0]
    assert ["categ_id.complete_name", "=ilike", "Hidráulica%"] in args[0]
    assert "description" in kwargs["fields"] and "standard_price" in kwargs["fields"]


async def test_warehouse_main_and_by_code(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    row = {"id": 1, "name": "YourCompany", "code": "WH", "lot_stock_id": [8, "WH/Stock"]}
    odoo.script += [LOGIN_OK, rpc_ok([row]), rpc_ok([])]
    repo = WarehouseRepo(client_factory())
    main = await repo.main()
    assert main.code == "WH" and main.lot_stock_id is not None and main.lot_stock_id.id == 8
    assert await repo.by_code("NOPE") is None
