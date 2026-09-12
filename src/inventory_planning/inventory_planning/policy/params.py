"""Planning parameters per product, with defaults by ABC class.

``planning_params`` (migration 005) holds what a planner may edit: service
level, review period, maximum coverage, and the lead-time mean and sigma
that phase 9 will measure. A product without a row gets the defaults of its
ABC class, computed from its share of revenue over the last year (A: the
top 80 percent, B: the next 15, C: the rest).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.base import StrictModel
from sc_core.shared.time import utc_now

AbcClass = Literal["A", "B", "C"]


class ClassParams(StrictModel):
    service_level: float
    review_period_days: int
    max_coverage_days: int


ABC_DEFAULTS: dict[AbcClass, ClassParams] = {
    "A": ClassParams(service_level=0.97, review_period_days=7, max_coverage_days=90),
    "B": ClassParams(service_level=0.95, review_period_days=7, max_coverage_days=120),
    "C": ClassParams(service_level=0.90, review_period_days=14, max_coverage_days=180),
}


class ProductParams(StrictModel):
    product_id: int
    abc_class: AbcClass
    service_level: float
    review_period_days: int
    max_coverage_days: int
    lead_time_mean_days: float | None = None  # measured (phase 9); None = supplier's promise
    lead_time_sigma_days: float | None = None  # measured; None = ratio x promise
    source: Literal["default", "planner", "measured"] = "default"

    @classmethod
    def default_for(cls, product_id: int, abc_class: AbcClass) -> ProductParams:
        base = ABC_DEFAULTS[abc_class]
        return cls(product_id=product_id, abc_class=abc_class, **base.model_dump())


def abc_classes(
    revenue_by_product: dict[int, float], *, a_share: float = 0.80, b_share: float = 0.95
) -> dict[int, AbcClass]:
    """Pareto classes by cumulative revenue share (A up to 80 percent, B to 95, C the rest)."""
    total = sum(v for v in revenue_by_product.values() if v > 0)
    classes: dict[int, AbcClass] = {}
    cumulative = 0.0
    for product_id, revenue in sorted(revenue_by_product.items(), key=lambda kv: (-kv[1], kv[0])):
        if total <= 0 or revenue <= 0:
            classes[product_id] = "C"
            continue
        cumulative += revenue
        share = cumulative / total
        classes[product_id] = "A" if share <= a_share else "B" if share <= b_share else "C"
    return classes


@runtime_checkable
class ParamsStore(Protocol):
    async def for_products(self, product_ids: list[int]) -> dict[int, ProductParams]:
        """Stored rows only; callers fill the rest with class defaults."""
        ...

    async def save(self, params: ProductParams) -> None: ...


class MemoryParamsStore:
    def __init__(self, rows: dict[int, ProductParams] | None = None) -> None:
        self.rows: dict[int, ProductParams] = dict(rows or {})

    async def for_products(self, product_ids: list[int]) -> dict[int, ProductParams]:
        return {pid: self.rows[pid] for pid in product_ids if pid in self.rows}

    async def save(self, params: ProductParams) -> None:
        self.rows[params.product_id] = params


class PostgresParamsStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def for_products(self, product_ids: list[int]) -> dict[int, ProductParams]:
        if not product_ids:
            return {}
        rows = await self._db.fetch_all(
            "SELECT product_id, abc_class, service_level, review_period_days, max_coverage_days, "
            "lead_time_mean_days, lead_time_sigma_days, source "
            "FROM planning_params WHERE product_id = ANY(%s)",
            (list(product_ids),),
        )
        return {int(r["product_id"]): ProductParams(**r) for r in rows}

    async def save(self, params: ProductParams) -> None:
        values: dict[str, Any] = params.model_dump()
        await self._db.execute(
            "INSERT INTO planning_params (product_id, abc_class, service_level, "
            "review_period_days, max_coverage_days, lead_time_mean_days, lead_time_sigma_days, "
            "source, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (product_id) DO UPDATE SET abc_class = EXCLUDED.abc_class, "
            "service_level = EXCLUDED.service_level, "
            "review_period_days = EXCLUDED.review_period_days, "
            "max_coverage_days = EXCLUDED.max_coverage_days, "
            "lead_time_mean_days = EXCLUDED.lead_time_mean_days, "
            "lead_time_sigma_days = EXCLUDED.lead_time_sigma_days, "
            "source = EXCLUDED.source, updated_at = EXCLUDED.updated_at",
            (
                values["product_id"],
                values["abc_class"],
                values["service_level"],
                values["review_period_days"],
                values["max_coverage_days"],
                values["lead_time_mean_days"],
                values["lead_time_sigma_days"],
                values["source"],
                utc_now(),
            ),
        )


def resolve_params(
    product_ids: list[int], stored: dict[int, ProductParams], classes: dict[int, AbcClass]
) -> dict[int, ProductParams]:
    """Stored parameters where they exist, class defaults elsewhere."""
    return {
        pid: stored.get(pid) or ProductParams.default_for(pid, classes.get(pid, "C"))
        for pid in product_ids
    }


def stamp() -> datetime:
    return utc_now()
