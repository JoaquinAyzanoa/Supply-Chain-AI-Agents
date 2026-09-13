"""``GET /performance/rank/{product_id}`` and ``GET /performance/scores`` behind the bearer.

The planner and the Control Tower read these: who to buy a product from,
and the latest score per supplier.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi_injector import Injected

from sc_core.infra.settings import Settings
from sc_core.schema.a2a import SupplierRanking, SupplierScore
from supplier_performance.ports import LivePerformancePorts
from supplier_performance.ranking import rank_suppliers

router = APIRouter(prefix="/performance", tags=["performance"])


def _check(settings: Settings, authorization: str) -> None:
    if not settings.a2a_token or authorization != f"Bearer {settings.a2a_token}":
        raise HTTPException(status_code=401, detail="bearer token required")


@router.get("/rank", response_model=list[SupplierRanking])
async def rank_many(
    product_ids: str = Query(..., description="comma-separated product ids"),
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    ports: LivePerformancePorts = Injected(LivePerformancePorts),
) -> list[SupplierRanking]:
    """One ranking per product, for a whole planning run at once (scores read once)."""
    _check(settings, authorization)
    try:
        ids = sorted({int(part) for part in product_ids.split(",") if part.strip()})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="product_ids must be integers") from exc
    if len(ids) > 500:
        raise HTTPException(status_code=422, detail="at most 500 products per call")
    scores = await ports.previous_scores()
    return [rank_suppliers(pid, await ports.price_list(pid), scores) for pid in ids]


@router.get("/rank/{product_id}", response_model=SupplierRanking)
async def rank(
    product_id: int,
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    ports: LivePerformancePorts = Injected(LivePerformancePorts),
) -> SupplierRanking:
    _check(settings, authorization)
    entries = await ports.price_list(product_id)
    scores = await ports.previous_scores()
    return rank_suppliers(product_id, entries, scores)


@router.get("/scores", response_model=list[SupplierScore])
async def scores(
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    ports: LivePerformancePorts = Injected(LivePerformancePorts),
) -> list[SupplierScore]:
    _check(settings, authorization)
    latest = await ports.previous_scores()
    return sorted(latest.values(), key=lambda s: (-s.score, s.partner_name))
