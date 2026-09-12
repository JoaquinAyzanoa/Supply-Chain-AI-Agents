"""Build the ``PlanningDataset``: a handful of bulk reads, then a join in memory."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from loguru import logger

from inventory_planning.models import PlanningDataset, ProductData
from inventory_planning.ports import DataPorts
from sc_core.odoo.models import DailyDemand


async def load_dataset(
    ports: DataPorts,
    *,
    as_of: date,
    history_days: int,
    category: str | None = None,
    warehouse_code: str | None = None,
    product_ids: Sequence[int] | None = None,
) -> PlanningDataset:
    """Every plannable product (or the given ones) with demand, stock, supply and terms."""
    started = time.perf_counter()
    warehouse = await ports.warehouse(warehouse_code)
    products = await ports.products(category)
    if product_ids is not None:
        wanted = set(product_ids)
        products = [p for p in products if p.id in wanted]
    ids = [p.id for p in products]
    history_start, history_end = PlanningDataset.history_window(as_of, history_days)

    demand = await ports.daily_sales(ids, since=history_start, warehouse_id=warehouse.id)
    on_hand = await ports.on_hand(ids, warehouse_id=warehouse.id)
    incoming = await ports.open_supply(ids, warehouse_id=warehouse.id)
    terms = await ports.supplier_terms(ids)
    orderpoints = await ports.orderpoints(warehouse.id)

    demand_by: dict[int, list[DailyDemand]] = defaultdict(list)
    for row in demand:
        if row.day <= history_end:
            demand_by[row.product_id].append(row)
    stock_by = {row.product_id: row for row in on_hand}
    incoming_by = defaultdict(list)
    for line in incoming:
        incoming_by[line.product_id].append(line)
    terms_by = defaultdict(list)
    for term in terms:
        terms_by[term.product_id].append(term)
    rule_by = {op.product_id.id: op for op in orderpoints}

    dataset = PlanningDataset(
        as_of=as_of,
        history_start=history_start,
        warehouse_id=warehouse.id,
        warehouse_code=warehouse.code,
        products=[
            ProductData(
                product_id=p.id,
                ref=p.ref,
                name=p.name,
                category=p.categ_id.name if p.categ_id else None,
                list_price=p.list_price,
                standard_price=p.standard_price,
                notes=p.description or None,
                demand=sorted(demand_by.get(p.id, []), key=lambda r: r.day),
                on_hand=stock_by[p.id].quantity if p.id in stock_by else 0.0,
                reserved=stock_by[p.id].reserved if p.id in stock_by else 0.0,
                incoming=incoming_by.get(p.id, []),
                suppliers=sorted(terms_by.get(p.id, []), key=lambda t: (t.sequence, t.price)),
                orderpoint=rule_by.get(p.id),
            )
            for p in products
        ],
    )
    logger.bind(
        products=len(dataset.products),
        demand_rows=len(demand),
        seconds=round(time.perf_counter() - started, 2),
    ).info("planning dataset loaded")
    return dataset
