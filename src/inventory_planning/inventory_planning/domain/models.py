"""What the planner works on: one dataset per run, one entry per product.

``PlanningDataset`` is built once by ``load_data`` from the Odoo
repositories and then read by every node. Demand is kept as daily buckets
(sparse: days without orders are absent) so the forecaster can re-bucket it
by week; stock, incoming lines, supplier terms and the current reorder rule
sit next to it.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import Field

from sc_core.odoo.models import DailyDemand, IncomingLine, Orderpoint, SupplierTerms
from sc_core.schema.base import StrictModel


class ProductData(StrictModel):
    product_id: int
    ref: str
    name: str
    category: str | None = None
    list_price: float = 0.0
    standard_price: float = 0.0
    notes: str | None = Field(default=None, description="internal notes on the product")
    demand: list[DailyDemand] = []
    on_hand: float = 0.0
    reserved: float = 0.0
    incoming: list[IncomingLine] = []
    suppliers: list[SupplierTerms] = []
    orderpoint: Orderpoint | None = None

    @property
    def incoming_qty(self) -> float:
        return sum(line.quantity for line in self.incoming)

    @property
    def preferred_supplier(self) -> SupplierTerms | None:
        return self.suppliers[0] if self.suppliers else None

    def weekly_demand(self, start: date, end: date) -> list[float]:
        """Ordered quantity per ISO week from ``start`` to ``end`` (inclusive), zeros kept."""
        weeks = max(1, ((end - start).days // 7) + 1)
        buckets = [0.0] * weeks
        for row in self.demand:
            if start <= row.day <= end:
                index = (row.day - start).days // 7
                if index < weeks:
                    buckets[index] += row.ordered
        return buckets

    def demand_between(self, start: date, end: date) -> float:
        return sum(r.ordered for r in self.demand if start <= r.day <= end)


class PlanningDataset(StrictModel):
    as_of: date
    history_start: date
    warehouse_id: int
    warehouse_code: str
    products: list[ProductData] = []

    @property
    def history_days(self) -> int:
        return (self.as_of - self.history_start).days

    def product(self, product_id: int) -> ProductData | None:
        return next((p for p in self.products if p.product_id == product_id), None)

    @classmethod
    def history_window(cls, as_of: date, history_days: int) -> tuple[date, date]:
        """Whole weeks ending the day before ``as_of`` (today's orders are incomplete)."""
        end = as_of - timedelta(days=1)
        weeks = max(1, history_days // 7)
        start = end - timedelta(days=weeks * 7 - 1)
        return start, end
