"""What the inventory planning agent reads from Odoo.

Four small repositories, all read-only:

* ``WarehouseRepo``: the warehouse to plan (one in the demo).
* ``ProductRepo``: the products worth planning (active, storable, purchasable,
  with at least one supplier; optionally one category subtree).
* ``DemandRepo``: demand history per product and day. The primary signal is
  what customers ordered (``sale.order.line`` in state sale/done, dated by
  the order) with what was actually delivered next to it; what shipped
  (outbound ``stock.move`` done) is available as a cross-check.
* ``QuantRepo`` / ``IncomingRepo``: stock on hand per product in the
  warehouse's internal locations, and confirmed purchase lines still to be
  received (qty, planned date, supplier).

Everything is aggregated here so the agent works on small typed rows, never
on raw Odoo records; 3,500 sales lines come back as a few hundred daily
buckets.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sc_core.odoo.client import Domain, OdooClient
from sc_core.odoo.models import (
    DailyDemand,
    IncomingLine,
    OnHand,
    Product,
    Quant,
    SupplierTerms,
    Warehouse,
    parse_iso,
    to_odoo_datetime,
)
from sc_core.odoo.repositories.base import Repo

SALE_STATES = ["sale", "done"]
BATCH = 500


class WarehouseRepo(Repo[Warehouse]):
    model = Warehouse

    async def list(self) -> list[Warehouse]:
        return await self.find([], order="id asc")

    async def by_code(self, code: str) -> Warehouse | None:
        return await self.find_one([["code", "=", code]])

    async def main(self) -> Warehouse:
        """The first warehouse, which is the only one in a single-site company."""
        found = await self.find([], limit=1, order="id asc")
        if not found:
            from sc_core.shared.errors import NotFound

            raise NotFound("no warehouse configured in Odoo")
        return found[0]


class ProductRepo(Repo[Product]):
    model = Product

    async def plannable(self, *, category: str | None = None) -> list[Product]:
        """Active, storable, purchasable products with a supplier (optionally one category)."""
        domain: Domain = [
            ["active", "=", True],
            ["purchase_ok", "=", True],
            ["is_storable", "=", True],
            ["seller_ids", "!=", False],
        ]
        if category:
            domain.append(["categ_id.complete_name", "=ilike", f"{category}%"])
        return await self.find(domain, order="default_code asc, id asc")


class DemandRepo:
    def __init__(self, client: OdooClient) -> None:
        self._c = client

    async def daily_sales(
        self, product_ids: Sequence[int], *, since: date, warehouse_id: int | None = None
    ) -> list[DailyDemand]:
        """Ordered and delivered quantities per product and order day, oldest first."""
        if not product_ids:
            return []
        domain: Domain = [
            ["product_id", "in", list(product_ids)],
            ["state", "in", SALE_STATES],
            ["order_id.date_order", ">=", to_odoo_datetime(_start_of(since))],
        ]
        if warehouse_id is not None:
            domain.append(["warehouse_id", "=", warehouse_id])
        lines = [
            row
            async for row in self._c.iter_search_read(
                "sale.order.line",
                domain,
                ["product_id", "product_uom_qty", "qty_delivered", "order_id"],
                batch_size=BATCH,
            )
        ]
        order_days = await self._order_days({int(r["order_id"][0]) for r in lines})
        totals: dict[tuple[int, date], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for row in lines:
            day = order_days.get(int(row["order_id"][0]))
            if day is None:
                continue
            bucket = totals[(int(row["product_id"][0]), day)]
            bucket[0] += float(row["product_uom_qty"] or 0.0)
            bucket[1] += float(row["qty_delivered"] or 0.0)
        return [
            DailyDemand(product_id=pid, day=day, ordered=ordered, delivered=delivered)
            for (pid, day), (ordered, delivered) in sorted(
                totals.items(), key=lambda kv: kv[0][::-1]
            )
        ]

    async def daily_shipped(self, product_ids: Sequence[int], *, since: date) -> list[DailyDemand]:
        """What left the warehouse to customers per product and day (cross-check signal)."""
        if not product_ids:
            return []
        domain: Domain = [
            ["product_id", "in", list(product_ids)],
            ["state", "=", "done"],
            ["location_dest_id.usage", "=", "customer"],
            ["date", ">=", to_odoo_datetime(_start_of(since))],
        ]
        totals: dict[tuple[int, date], float] = defaultdict(float)
        async for row in self._c.iter_search_read(
            "stock.move", domain, ["product_id", "quantity", "date"], batch_size=BATCH
        ):
            day = parse_iso(row["date"]).date()
            totals[(int(row["product_id"][0]), day)] += float(row["quantity"] or 0.0)
        return [
            DailyDemand(product_id=pid, day=day, ordered=qty, delivered=qty)
            for (pid, day), qty in sorted(totals.items(), key=lambda kv: kv[0][::-1])
        ]

    async def _order_days(self, order_ids: set[int]) -> dict[int, date]:
        days: dict[int, date] = {}
        ids = sorted(order_ids)
        for start in range(0, len(ids), BATCH):
            rows = await self._c.read("sale.order", ids[start : start + BATCH], ["date_order"])
            for row in rows:
                if row.get("date_order"):
                    days[int(row["id"])] = parse_iso(row["date_order"]).date()
        return days


class QuantRepo(Repo[Quant]):
    model = Quant

    async def on_hand(self, product_ids: Sequence[int], *, warehouse_id: int) -> list[OnHand]:
        """Quantity and reservations per product in the warehouse's internal locations."""
        if not product_ids:
            return []
        rows = await self.find(
            [
                ["product_id", "in", list(product_ids)],
                ["location_id.usage", "=", "internal"],
                ["warehouse_id", "=", warehouse_id],
            ]
        )
        totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for quant in rows:
            totals[quant.product_id.id][0] += quant.quantity
            totals[quant.product_id.id][1] += quant.reserved_quantity
        return [
            OnHand(product_id=pid, warehouse_id=warehouse_id, quantity=q, reserved=r)
            for pid, (q, r) in sorted(totals.items())
        ]


class IncomingRepo:
    def __init__(self, client: OdooClient) -> None:
        self._c = client

    async def open_supply(
        self, product_ids: Sequence[int], *, warehouse_id: int | None = None
    ) -> list[IncomingLine]:
        """Confirmed purchase lines with something still to receive, soonest first."""
        if not product_ids:
            return []
        domain: Domain = [
            ["product_id", "in", list(product_ids)],
            ["state", "=", "purchase"],
        ]
        if warehouse_id is not None:
            domain.append(["order_id.picking_type_id.warehouse_id", "=", warehouse_id])
        lines: list[IncomingLine] = []
        async for row in self._c.iter_search_read(
            "purchase.order.line",
            domain,
            ["product_id", "product_qty", "qty_received", "date_planned", "order_id", "partner_id"],
            batch_size=BATCH,
            order="date_planned asc, id asc",
        ):
            open_qty = float(row["product_qty"] or 0.0) - float(row["qty_received"] or 0.0)
            if open_qty <= 0:
                continue
            lines.append(
                IncomingLine(
                    line_id=int(row["id"]),
                    product_id=int(row["product_id"][0]),
                    po_id=int(row["order_id"][0]),
                    po_name=str(row["order_id"][1]),
                    partner_id=int(row["partner_id"][0]) if row.get("partner_id") else None,
                    quantity=open_qty,
                    date_planned=parse_iso(row["date_planned"]).date()
                    if row.get("date_planned")
                    else None,
                )
            )
        return lines


async def supplier_terms(client: OdooClient, product_ids: Sequence[int]) -> list[SupplierTerms]:
    """Every supplier entry for the products, best (lowest sequence, then price) first.

    Entries hang on the template; the variant's own entries come along.
    """
    if not product_ids:
        return []
    products = await client.read(
        "product.product", list(product_ids), ["product_tmpl_id", "default_code"]
    )
    by_template: dict[int, list[int]] = defaultdict(list)
    for row in products:
        by_template[int(row["product_tmpl_id"][0])].append(int(row["id"]))
    rows = await client.search_read(
        "product.supplierinfo",
        [["product_tmpl_id", "in", list(by_template)]],
        [
            "partner_id",
            "product_tmpl_id",
            "product_id",
            "min_qty",
            "price",
            "currency_id",
            "delay",
            "sequence",
        ],
        order="sequence asc, price asc, id asc",
    )
    terms: list[SupplierTerms] = []
    for row in rows:
        template_id = int(row["product_tmpl_id"][0])
        variant: Any = row.get("product_id")
        targets = [int(variant[0])] if variant else by_template.get(template_id, [])
        for pid in targets:
            if pid not in product_ids:
                continue
            terms.append(
                SupplierTerms(
                    product_id=pid,
                    partner_id=int(row["partner_id"][0]),
                    partner_name=str(row["partner_id"][1]),
                    delay_days=int(row["delay"] or 0),
                    min_qty=float(row["min_qty"] or 0.0),
                    price=float(row["price"] or 0.0),
                    currency=str(row["currency_id"][1]) if row.get("currency_id") else None,
                    sequence=int(row["sequence"] or 0),
                )
            )
    return terms


def _start_of(day: date) -> datetime:
    return datetime(day.year, day.month, day.day)
