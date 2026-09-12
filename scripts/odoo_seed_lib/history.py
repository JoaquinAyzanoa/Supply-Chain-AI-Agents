"""Write the plan into Odoo: opening stock, reorder rules, delivered sales
orders, received purchase orders and today's open supply, all in date order.

Odoo stamps "now" on validated moves and confirmed orders; the historical
dates are written back afterwards (the ORM accepts it). Every order carries
a natural key (``client_order_ref`` SEED-SO-n, ``sc_external_ref``
seed-po-n) that is checked before creating, so a second run creates nothing.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from odoo_seed_lib.catalogue import ProductIds
from odoo_seed_lib.client import SeedClient
from odoo_seed_lib.dataset import Dataset
from odoo_seed_lib.plan import Delivery, Plan, Purchase, build_plan, product_delay

# Keys carry the seed and the span so a smoke run (--months 1 --seed 1) and the
# full run can coexist without one skipping the other's records.
SO_REF = "SEED-SO-{seed}-{months}-{n}"
PO_REF = "seed-po-{seed}-{months}-{n}"
OPEN_REF = "seed-open-{seed}-{n}"


def stamp(day: date, hour: int = 10) -> str:
    return datetime(day.year, day.month, day.day, hour, 0, 0).strftime("%Y-%m-%d %H:%M:%S")


class Warehouse:
    def __init__(self, wh_id: int, stock_location_id: int) -> None:
        self.id = wh_id
        self.stock_location_id = stock_location_id


async def load_warehouse(client: SeedClient, code: str) -> Warehouse:
    rows = await client.odoo.search_read(
        "stock.warehouse", [["code", "=", code]], ["id", "lot_stock_id"], limit=1
    )
    if not rows:
        raise RuntimeError(f"warehouse {code!r} not found")
    return Warehouse(int(rows[0]["id"]), int(rows[0]["lot_stock_id"][0]))


async def run(
    client: SeedClient,
    ds: Dataset,
    products: dict[str, ProductIds],
    suppliers: dict[str, int],
    customers: dict[str, int],
    *,
    stages: list[str],
    today: date | None = None,
) -> Plan:
    plan = build_plan(ds, today=today)
    wh = await load_warehouse(client, ds.warehouse)
    print(
        f"plan: {len(plan.deliveries)} deliveries, {len(plan.purchases)} purchases, "
        f"{plan.start} .. {plan.end}"
    )
    if "stock" in stages:
        await opening_stock(client, ds, plan, products, wh)
        await reorder_rules(client, ds, plan, products, wh)
    if "demand" in stages or "supply" in stages:
        await chronological(client, ds, plan, products, suppliers, customers, wh, stages)
    if "supply" in stages:
        await open_supply(client, ds, plan, products, suppliers, wh)
    if "demand" in stages or "supply" in stages:
        await repair_open_pickings(client)
    return plan


# --- stock ---------------------------------------------------------------------


async def opening_stock(
    client: SeedClient, ds: Dataset, plan: Plan, products: dict[str, ProductIds], wh: Warehouse
) -> None:
    odoo = client.odoo
    for code, qty in plan.opening.items():
        product_id = products[code].product_id
        existing = await odoo.search(
            "stock.move",
            [["product_id", "=", product_id], ["is_inventory", "=", True], ["state", "=", "done"]],
            limit=1,
        )
        if existing:
            client.found["stock.quant"] = client.found.get("stock.quant", 0) + 1
            continue
        quant_id = await odoo.create(
            "stock.quant",
            {
                "product_id": product_id,
                "location_id": wh.stock_location_id,
                "inventory_quantity": qty,
            },
        )
        await odoo.call("stock.quant", "action_apply_inventory", [quant_id])
        moves = await odoo.search(
            "stock.move",
            [["product_id", "=", product_id], ["is_inventory", "=", True]],
            order="id desc",
            limit=1,
        )
        if moves:
            await _backdate_moves(client, moves, plan.start - timedelta(days=1))
        client.created["stock.quant"] = client.created.get("stock.quant", 0) + 1


async def reorder_rules(
    client: SeedClient, ds: Dataset, plan: Plan, products: dict[str, ProductIds], wh: Warehouse
) -> None:
    for p in ds.products:
        flaw = ds.rule_flaws.get(p.code)
        if flaw is not None and flaw.no_rule:
            continue
        min_weeks = flaw.min_weeks if flaw and flaw.min_weeks is not None else p.stock.min_weeks
        max_weeks = flaw.max_weeks if flaw and flaw.max_weeks is not None else p.stock.max_weeks
        values = {
            "product_id": products[p.code].product_id,
            "warehouse_id": wh.id,
            "location_id": wh.stock_location_id,
            "product_min_qty": math.ceil(p.demand.mean_weekly * min_weeks),
            "product_max_qty": math.ceil(p.demand.mean_weekly * max_weeks),
            "trigger": "auto",
        }
        await client.find_or_create(
            "stock.warehouse.orderpoint",
            [["product_id", "=", values["product_id"]], ["warehouse_id", "=", wh.id]],
            values,
        )


# --- chronological history --------------------------------------------------------


async def chronological(
    client: SeedClient,
    ds: Dataset,
    plan: Plan,
    products: dict[str, ProductIds],
    suppliers: dict[str, int],
    customers: dict[str, int],
    wh: Warehouse,
    stages: list[str],
) -> None:
    """Day by day: purchases ordered that day first, then the day's deliveries as one batch."""
    purchases_by_day: dict[date, list[Purchase]] = {}
    deliveries_by_day: dict[date, list[Delivery]] = {}
    if "supply" in stages:
        for p in plan.purchases:
            purchases_by_day.setdefault(p.ordered, []).append(p)
    if "demand" in stages:
        for d in plan.deliveries:
            deliveries_by_day.setdefault(d.day, []).append(d)
    days = sorted(set(purchases_by_day) | set(deliveries_by_day))
    total = len(plan.purchases) * ("supply" in stages) + len(plan.deliveries) * ("demand" in stages)
    done = 0
    for day in days:
        for p in purchases_by_day.get(day, []):
            await purchase(client, ds, p, products, suppliers, wh)
            done += 1
        group = deliveries_by_day.get(day, [])
        if group:
            await deliveries_batch(client, ds, group, products, customers)
            done += len(group)
        if days.index(day) % 20 == 0 or day == days[-1]:
            print(f"  history: {done}/{total} ({day})", flush=True)


async def deliveries_batch(
    client: SeedClient,
    ds: Dataset,
    group: list[Delivery],
    products: dict[str, ProductIds],
    customers: dict[str, int],
) -> None:
    """All deliveries of one day: create, confirm, validate and backdate in batches."""
    odoo = client.odoo
    day = group[0].day
    refs = {SO_REF.format(seed=ds.seed, months=ds.history.months, n=d.index): d for d in group}
    existing = await odoo.search_read(
        "sale.order", [["client_order_ref", "in", list(refs)]], ["client_order_ref"]
    )
    done_refs = {r["client_order_ref"] for r in existing}
    client.found["sale.order"] = client.found.get("sale.order", 0) + len(done_refs)
    todo = [(ref, d) for ref, d in refs.items() if ref not in done_refs]
    if not todo:
        return
    values = [
        {
            "partner_id": customers[d.customer],
            "date_order": stamp(day, 9),
            "client_order_ref": ref,
            "order_line": [
                (
                    0,
                    0,
                    {
                        "product_id": products[d.code].product_id,
                        "product_uom_qty": d.qty,
                        "price_unit": ds.product(d.code).list_price,
                    },
                )
            ],
        }
        for ref, d in todo
    ]
    so_ids = [int(i) for i in await odoo.execute("sale.order", "create", values)]
    await odoo.call("sale.order", "action_confirm", so_ids)
    await odoo.write("sale.order", so_ids, {"date_order": stamp(day, 9)})
    shipped_by_so = {
        so_id: d.qty - (d.qty // 2 if d.backorder else 0)
        for so_id, (_ref, d) in zip(so_ids, todo, strict=True)
    }
    backorder_sos = {so_id for so_id, (_ref, d) in zip(so_ids, todo, strict=True) if d.backorder}
    pickings = await odoo.search_read(
        "stock.picking", [["sale_id", "in", so_ids]], ["id", "sale_id"]
    )
    picking_so = {int(p["id"]): int(p["sale_id"][0]) for p in pickings}
    moves = await odoo.search_read(
        "stock.move", [["picking_id", "in", list(picking_so)]], ["id", "picking_id"]
    )
    by_qty: dict[float, list[int]] = {}
    for m in moves:
        qty = shipped_by_so[picking_so[int(m["picking_id"][0])]]
        by_qty.setdefault(qty, []).append(int(m["id"]))
    for qty, ids in by_qty.items():
        await odoo.write("stock.move", ids, {"quantity": qty, "picked": True})
    picking_ids = list(picking_so)
    await odoo.write("stock.picking", picking_ids, {"scheduled_date": stamp(day, 8)})
    full = [pid for pid, so in picking_so.items() if so not in backorder_sos]
    partial = [pid for pid, so in picking_so.items() if so in backorder_sos]
    if full:
        await validate(client, full, backorder=False)
    if partial:
        await validate(client, partial, backorder=True)
    await _backdate_moves(client, [int(m["id"]) for m in moves], day)
    done_pickings = await odoo.search(
        "stock.picking", [["id", "in", picking_ids], ["state", "=", "done"]]
    )
    if done_pickings:
        await odoo.write("stock.picking", done_pickings, {"date_done": stamp(day, 15)})
    client.created["sale.order"] = client.created.get("sale.order", 0) + len(todo)


async def purchase(
    client: SeedClient,
    ds: Dataset,
    p: Purchase,
    products: dict[str, ProductIds],
    suppliers: dict[str, int],
    wh: Warehouse,
) -> None:
    odoo = client.odoo
    ref = PO_REF.format(seed=ds.seed, months=ds.history.months, n=p.index)
    if await client.find_id("purchase.order", [["sc_external_ref", "=", ref]]):
        client.found["purchase.order"] = client.found.get("purchase.order", 0) + 1
        return
    po_id = await _create_confirmed_po(
        client, ds, p.supplier, suppliers[p.supplier], p.lines, products, p.ordered, p.planned, ref
    )
    pickings = await odoo.search("stock.picking", [["purchase_id", "=", po_id]], order="id asc")
    if not pickings:
        return
    quantities = {products[code].product_id: qty for code, qty in p.lines}
    if p.outcome == "partial":
        first = {pid: qty // 2 for pid, qty in quantities.items()}
        await _validate_picking(client, pickings[0], first, p.received, backorder=True)
        if p.received_rest is not None:
            rest = await odoo.search(
                "stock.picking",
                [["purchase_id", "=", po_id], ["state", "!=", "done"]],
                order="id asc",
            )
            if rest:
                remaining = {pid: qty - first[pid] for pid, qty in quantities.items()}
                await _validate_picking(client, rest[0], remaining, p.received_rest)
    else:
        await _validate_picking(client, pickings[0], quantities, p.received)
    client.created["purchase.order"] = client.created.get("purchase.order", 0) + 1


async def _create_confirmed_po(
    client: SeedClient,
    ds: Dataset,
    supplier_key: str,
    partner_id: int,
    lines: tuple[tuple[str, int], ...] | list[tuple[str, int]],
    products: dict[str, ProductIds],
    ordered: date,
    planned: date,
    ref: str,
) -> int:
    odoo = client.odoo
    order_lines = []
    for code, qty in lines:
        product = ds.product(code)
        terms = product.suppliers[supplier_key]
        order_lines.append(
            (
                0,
                0,
                {
                    "product_id": products[code].product_id,
                    "product_qty": qty,
                    "price_unit": round(product.list_price * terms.price_ratio, 2),
                    "date_planned": stamp(planned, 12),
                },
            )
        )
    po_id = await odoo.create(
        "purchase.order",
        {
            "partner_id": partner_id,
            "date_order": stamp(ordered, 11),
            "origin": f"SEED/{ordered:%Y-%m}",
            "sc_external_ref": ref,
            "order_line": order_lines,
        },
    )
    await odoo.call("purchase.order", "button_confirm", [po_id])
    await odoo.write(
        "purchase.order",
        [po_id],
        {"date_order": stamp(ordered, 11), "date_approve": stamp(ordered, 11)},
    )
    return po_id


async def _validate_picking(
    client: SeedClient,
    picking_id: int,
    quantities: dict[int, float],
    day: date,
    *,
    backorder: bool = False,
) -> None:
    """Set done quantities, validate (creating a backorder when partial), backdate."""
    odoo = client.odoo
    moves = await odoo.search_read(
        "stock.move", [["picking_id", "=", picking_id]], ["id", "product_id", "product_uom_qty"]
    )
    for move in moves:
        product_id = int(move["product_id"][0])
        qty = quantities.get(product_id, move["product_uom_qty"])
        await odoo.write("stock.move", [move["id"]], {"quantity": qty, "picked": True})
    # the scheduled date is frozen once the transfer is done: set it first
    await odoo.write("stock.picking", [picking_id], {"scheduled_date": stamp(day, 8)})
    await validate(client, [picking_id], backorder=backorder)
    move_ids = [m["id"] for m in moves]
    await _backdate_moves(client, move_ids, day)
    await odoo.write("stock.picking", [picking_id], {"date_done": stamp(day, 15)})


async def validate(client: SeedClient, picking_ids: list[int], *, backorder: bool) -> None:
    """``button_validate`` without the backorder wizard.

    ``skip_backorder`` makes Odoo decide by itself: a backorder for what was
    not received (``backorder=True``) or none at all when the picking is in
    ``picking_ids_not_to_backorder``.
    """
    context: dict[str, Any] = {"skip_backorder": True}
    if not backorder:
        context["picking_ids_not_to_backorder"] = picking_ids
    await client.odoo.call("stock.picking", "button_validate", picking_ids, context=context)


async def repair_open_pickings(client: SeedClient) -> int:
    """Finish seeded pickings an earlier run left half done (quantities set, never validated).

    Receipts get validated on the date they were meant to and their backorder a
    week later; customer deliveries keep their backorder open (that is the
    3 % of partial deliveries by design).
    """
    odoo = client.odoo
    stuck = await odoo.search_read(
        "stock.picking",
        [
            ["state", "in", ["assigned", "confirmed"]],
            ["date_done", "!=", False],
            "|",
            ["purchase_id.sc_external_ref", "like", "seed-po-%"],
            ["sale_id.client_order_ref", "like", "SEED-SO-%"],
        ],
        ["id", "date_done", "picking_type_code", "purchase_id"],
    )
    fixed = 0
    for picking in stuck:
        day = date.fromisoformat(str(picking["date_done"])[:10])
        await validate(client, [int(picking["id"])], backorder=True)
        moves = await odoo.search("stock.move", [["picking_id", "=", int(picking["id"])]])
        await _backdate_moves(client, moves, day)
        await odoo.write("stock.picking", [int(picking["id"])], {"date_done": stamp(day, 15)})
        fixed += 1
        if picking["picking_type_code"] == "incoming":
            rest = await odoo.search(
                "stock.picking",
                [["backorder_id", "=", int(picking["id"])], ["state", "!=", "done"]],
            )
            if rest:
                rest_day = day + timedelta(days=7)
                remaining = await odoo.search_read(
                    "stock.move", [["picking_id", "in", rest]], ["id", "product_uom_qty"]
                )
                for move in remaining:
                    await odoo.write(
                        "stock.move",
                        [move["id"]],
                        {"quantity": move["product_uom_qty"], "picked": True},
                    )
                await odoo.write("stock.picking", rest, {"scheduled_date": stamp(rest_day, 8)})
                await validate(client, rest, backorder=False)
                await _backdate_moves(client, [m["id"] for m in remaining], rest_day)
                await odoo.write("stock.picking", rest, {"date_done": stamp(rest_day, 15)})
    if fixed:
        print(f"  repaired {fixed} half-done picking(s) from an earlier run")
    return fixed


async def _backdate_moves(client: SeedClient, move_ids: list[int], day: date) -> None:
    odoo = client.odoo
    when = stamp(day, 15)
    await odoo.write("stock.move", move_ids, {"date": when})
    line_ids = await odoo.search("stock.move.line", [["move_id", "in", move_ids]])
    if line_ids:
        await odoo.write("stock.move.line", line_ids, {"date": when})


# --- open supply (today) ---------------------------------------------------------


async def open_supply(
    client: SeedClient,
    ds: Dataset,
    plan: Plan,
    products: dict[str, ProductIds],
    suppliers: dict[str, int],
    wh: Warehouse,
) -> None:
    for n, entry in enumerate(ds.open_supply, 1):
        ref = OPEN_REF.format(seed=ds.seed, n=n)
        if await client.find_id("purchase.order", [["sc_external_ref", "=", ref]]):
            client.found["purchase.order"] = client.found.get("purchase.order", 0) + 1
            continue
        lines = []
        for code in entry.products:
            product = ds.product(code)
            terms = product.suppliers.get(entry.supplier) or product.suppliers["primary"]
            qty = max(math.ceil(product.demand.mean_weekly * 4), terms.min_qty)
            lines.append((code, int(qty)))
        planned = plan.end + timedelta(days=entry.days_ahead)
        delay = product_delay(ds, entry.supplier, lines)
        ordered = planned - timedelta(days=delay)
        await _create_confirmed_po(
            client,
            ds,
            entry.supplier,
            suppliers[entry.supplier],
            lines,
            products,
            ordered,
            planned,
            ref,
        )
        client.created["purchase.order"] = client.created.get("purchase.order", 0) + 1
