"""Demand history for one product, for the Control Tower's sparkline.

``GET /planning/demand/{product_id}?days=90`` answers one point per calendar
day (zeros where nothing was ordered) so the chart needs no gap filling. The
route sits behind the same bearer token as the A2A endpoint: only the
director calls it, on behalf of a signed-in user.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi_injector import Injected

from inventory_planning.graph import Deps
from sc_core.infra.settings import Settings
from sc_core.schema.base import StrictModel

router = APIRouter(prefix="/planning", tags=["planning"])


class DemandDay(StrictModel):
    day: date
    ordered: float
    delivered: float


class DemandHistory(StrictModel):
    product_id: int
    warehouse_id: int
    since: date
    until: date
    days: list[DemandDay]


@router.get("/demand/{product_id}", response_model=DemandHistory)
async def demand_history(
    product_id: int,
    days: int = Query(default=90, ge=7, le=730),
    warehouse_code: str | None = Query(default=None),
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    deps: Deps = Injected(Deps),
) -> DemandHistory:
    if not settings.a2a_token or authorization != f"Bearer {settings.a2a_token}":
        raise HTTPException(status_code=401, detail="bearer token required")
    until = deps.today()
    since = until - timedelta(days=days)
    warehouse = await deps.data.warehouse(warehouse_code or deps.cfg.warehouse_code or None)
    rows = await deps.data.daily_sales([product_id], since=since, warehouse_id=warehouse.id)
    by_day: dict[date, DemandDay] = {}
    for row in rows:
        current = by_day.get(row.day)
        by_day[row.day] = DemandDay(
            day=row.day,
            ordered=(current.ordered if current else 0.0) + row.ordered,
            delivered=(current.delivered if current else 0.0) + row.delivered,
        )
    calendar = [since + timedelta(days=i) for i in range((until - since).days + 1)]
    return DemandHistory(
        product_id=product_id,
        warehouse_id=warehouse.id,
        since=since,
        until=until,
        days=[
            by_day.get(day) or DemandDay(day=day, ordered=0.0, delivered=0.0) for day in calendar
        ],
    )
