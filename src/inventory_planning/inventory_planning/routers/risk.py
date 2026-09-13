"""``GET /planning/risk``: the risk radar, computed on demand from live data.

Behind the same bearer as the demand history; the director serves it to the
Control Tower. Loads the planner's dataset, forecasts every product (with
the demand calendar) and reads the latest supplier scorecards.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi_injector import Injected

from inventory_planning.graph import Deps
from inventory_planning.nodes.forecast import forecast_all
from inventory_planning.nodes.load_data import load_dataset
from inventory_planning.risk import RiskReport, risk_report
from sc_core.infra.db import Database
from sc_core.infra.settings import Settings

router = APIRouter(prefix="/planning", tags=["planning"])


async def latest_scores(db: Database) -> dict[int, dict[str, Any]]:
    """The latest scorecard per supplier, as a plain dict per partner id."""
    try:
        rows = await db.fetch_all(
            "SELECT DISTINCT ON (partner_id) partner_id, partner_name, otif, "
            "lead_time_sigma_days, score FROM supplier_scores "
            "ORDER BY partner_id, computed_at DESC"
        )
    except Exception:  # the table is the performance agent's; the radar works without it
        return {}
    return {
        int(r["partner_id"]): {
            "partner_name": r.get("partner_name"),
            "otif": float(r["otif"]) if r.get("otif") is not None else None,
            "lead_time_sigma_days": (
                float(r["lead_time_sigma_days"])
                if r.get("lead_time_sigma_days") is not None
                else None
            ),
            "score": float(r["score"]) if r.get("score") is not None else None,
        }
        for r in rows
    }


@router.get("/risk", response_model=RiskReport)
async def risk(
    warehouse_code: str | None = Query(default=None),
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    deps: Deps = Injected(Deps),
    db: Database = Injected(Database),
) -> RiskReport:
    if not settings.a2a_token or authorization != f"Bearer {settings.a2a_token}":
        raise HTTPException(status_code=401, detail="bearer token required")
    as_of = deps.today()
    dataset = await load_dataset(
        deps.data,
        as_of=as_of,
        history_days=deps.cfg.history_days,
        category=deps.cfg.product_category or None,
        warehouse_code=warehouse_code or deps.cfg.warehouse_code or None,
        product_ids=None,
    )
    events = await deps.calendar.events(since=dataset.history_start) if deps.calendar else []
    # the calendar already raised the forecast rate; the radar adds nothing on top
    forecasts = forecast_all(dataset, events)
    return risk_report(dataset, forecasts, await latest_scores(db))
