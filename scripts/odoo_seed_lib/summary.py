"""Sanity report after seeding: stock, demand shape, supplier performance, open supply.

Read back from Odoo, not from the plan, so it shows what the agents will see.
"""

from __future__ import annotations

from datetime import datetime

from odoo_seed_lib.catalogue import ProductIds
from odoo_seed_lib.client import SeedClient
from odoo_seed_lib.dataset import Dataset


async def report(
    client: SeedClient,
    ds: Dataset,
    products: dict[str, ProductIds],
    suppliers: dict[str, int],
) -> str:
    odoo = client.odoo
    lines = ["summary:"]

    # on hand per product
    product_ids = {v.product_id: k for k, v in products.items()}
    quants = await odoo.search_read(
        "stock.quant",
        [["product_id", "in", list(product_ids)], ["location_id.usage", "=", "internal"]],
        ["product_id", "quantity"],
    )
    on_hand: dict[str, float] = dict.fromkeys(products, 0.0)
    for q in quants:
        on_hand[product_ids[int(q["product_id"][0])]] += float(q["quantity"])
    negatives = [c for c, q in on_hand.items() if q < 0]
    lines.append(
        "  on hand: "
        + ", ".join(f"{c} {int(q)}" for c, q in on_hand.items())
        + (f"  NEGATIVE: {negatives}" if negatives else "")
    )

    # outgoing moves per month for one product (demand shape)
    code = "CBEA-LHN" if "CBEA-LHN" in products else next(iter(products))
    moves = await odoo.search_read(
        "stock.move",
        [
            ["product_id", "=", products[code].product_id],
            ["state", "=", "done"],
            ["location_dest_id.usage", "=", "customer"],
        ],
        ["date", "quantity"],
    )
    by_month: dict[str, float] = {}
    for m in moves:
        key = str(m["date"])[:7]
        by_month[key] = by_month.get(key, 0) + float(m["quantity"])
    months = sorted(by_month)
    lines.append(
        f"  {code} shipped per month ({len(months)} months): "
        + " ".join(f"{k[2:]}:{int(v)}" for k, v in sorted(by_month.items())[-12:])
    )

    # supplier performance from receipts: on time = received on or before planned
    for key, partner_id in suppliers.items():
        pickings = await odoo.search_read(
            "stock.picking",
            [
                ["partner_id", "=", partner_id],
                ["state", "=", "done"],
                ["picking_type_code", "=", "incoming"],
            ],
            ["scheduled_date", "date_done", "purchase_id"],
        )
        if not pickings:
            lines.append(f"  supplier {key}: no receipts")
            continue
        on_time = 0
        lead_days: list[float] = []
        po_ids = {int(p["purchase_id"][0]) for p in pickings if p["purchase_id"]}
        orders = await odoo.read("purchase.order", list(po_ids), ["date_order", "date_planned"])
        planned_by_po = {int(o["id"]): o for o in orders}
        for p in pickings:
            po = planned_by_po.get(int(p["purchase_id"][0])) if p["purchase_id"] else None
            if not po:
                continue
            planned = _dt(po["date_planned"])
            done = _dt(p["date_done"])
            ordered = _dt(po["date_order"])
            if done.date() <= planned.date():
                on_time += 1
            lead_days.append((done - ordered).days)
        otif = 100 * on_time / len(pickings)
        mean_lead = sum(lead_days) / len(lead_days) if lead_days else 0
        lines.append(
            f"  supplier {key}: {len(pickings)} receipts, on time {otif:.0f} %, "
            f"observed lead time {mean_lead:.0f} days"
        )

    # open supply
    open_orders = await odoo.search_read(
        "purchase.order",
        [
            ["state", "=", "purchase"],
            ["receipt_status", "!=", "full"],
            ["partner_id", "in", list(suppliers.values())],
        ],
        ["name", "date_planned"],
    )
    lines.append(
        f"  open incoming orders: {len(open_orders)} "
        + " ".join(f"{o['name']}({str(o['date_planned'])[:10]})" for o in open_orders)
    )
    rules = await odoo.search_count(
        "stock.warehouse.orderpoint", [["product_id", "in", list(product_ids)]]
    )
    lines.append(f"  reorder rules: {rules} of {len(products)} products")
    return "\n".join(lines)


def _dt(value: str) -> datetime:
    return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
