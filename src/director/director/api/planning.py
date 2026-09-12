"""Planning runs for review: the proposal's lines and what-if simulations.

The director reads ``planning_runs`` / ``planning_lines`` (the planner's
tables in the shared application database) and rebuilds each
``ReplenishmentLine`` from the stored inputs and outputs, with what was
accepted and applied. A what-if sends the planner a ``what_if`` task for
that product with the overrides; the planner recomputes without writing
and the recomputed line comes back next to the baseline.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger
from pydantic import ValidationError

from director.agents import Agents
from director.api.auth import Approver, Principal, Viewer
from sc_core.infra.db import Database
from sc_core.schema.a2a import InventoryPlanningResult, InventoryPlanningTask, PlanningOverrides
from sc_core.schema.base import StrictModel
from sc_core.schema.planning import ReplenishmentLine
from sc_core.shared.errors import ScError
from sc_core.shared.idempotency import new_id

router = APIRouter(prefix="/planning", tags=["planning"])


class PlanningRunRow(StrictModel):
    run_id: str
    case_id: str
    kind: str
    as_of: date
    warehouse_id: int
    status: str
    approval_id: int | None = None
    summary: str | None = None
    totals: dict[str, float] = {}
    created_at: datetime
    updated_at: datetime


class PlanningLineRow(StrictModel):
    line: ReplenishmentLine
    accepted: bool | None = None
    applied: dict[str, Any] | None = None
    applied_at: datetime | None = None


class PlanningRunDetail(StrictModel):
    run: PlanningRunRow
    lines: list[PlanningLineRow]


class WhatIfRequest(StrictModel):
    line_id: str
    overrides: PlanningOverrides


class WhatIfResponse(StrictModel):
    baseline: ReplenishmentLine
    simulated: ReplenishmentLine
    run_id: str


@runtime_checkable
class PlanningReadStore(Protocol):
    async def runs(self, *, limit: int = 30) -> list[PlanningRunRow]: ...

    async def run(self, run_id: str) -> PlanningRunRow | None: ...

    async def lines(self, run_id: str) -> list[PlanningLineRow]: ...


class PostgresPlanningReadStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def runs(self, *, limit: int = 30) -> list[PlanningRunRow]:
        rows = await self._db.fetch_all(
            "SELECT * FROM planning_runs ORDER BY created_at DESC LIMIT %s", (limit,)
        )
        return [_run_row(r) for r in rows]

    async def run(self, run_id: str) -> PlanningRunRow | None:
        row = await self._db.fetch_one("SELECT * FROM planning_runs WHERE run_id = %s", (run_id,))
        return _run_row(row) if row else None

    async def lines(self, run_id: str) -> list[PlanningLineRow]:
        rows = await self._db.fetch_all(
            "SELECT * FROM planning_lines WHERE run_id = %s ORDER BY product_ref, id", (run_id,)
        )
        return [line_row_from_db(r) for r in rows]


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _run_row(row: dict[str, Any]) -> PlanningRunRow:
    return PlanningRunRow(
        run_id=str(row["run_id"]),
        case_id=str(row["case_id"]),
        kind=str(row["kind"]),
        as_of=row["as_of"],
        warehouse_id=int(row["warehouse_id"]),
        status=str(row["status"]),
        approval_id=row.get("approval_id"),
        summary=row.get("summary"),
        totals={k: float(v) for k, v in (_json(row.get("totals")) or {}).items()},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def line_row_from_db(row: dict[str, Any]) -> PlanningLineRow:
    """A ``ReplenishmentLine`` from the stored inputs and outputs (the planner's ``line_row``)."""
    line = ReplenishmentLine(
        line_id=str(row["line_id"]),
        product_id=int(row["product_id"]),
        product_ref=str(row["product_ref"]),
        warehouse_id=int(row["warehouse_id"]),
        action=row["action"],
        exception=row.get("exception"),
        explanation=row.get("explanation"),
        **_json(row["inputs"]),
        **_json(row["outputs"]),
    )
    applied = _json(row.get("applied"))
    return PlanningLineRow(
        line=line, accepted=row.get("accepted"), applied=applied, applied_at=row.get("applied_at")
    )


@router.get("/runs", response_model=list[PlanningRunRow])
async def list_planning_runs(
    limit: int = Query(default=30, ge=1, le=200),
    _: Principal = Viewer,
    store: PlanningReadStore = Injected(PlanningReadStore),  # type: ignore[type-abstract]
) -> list[PlanningRunRow]:
    return await store.runs(limit=limit)


@router.get("/runs/{run_id}", response_model=PlanningRunDetail)
async def get_planning_run(
    run_id: str,
    _: Principal = Viewer,
    store: PlanningReadStore = Injected(PlanningReadStore),  # type: ignore[type-abstract]
) -> PlanningRunDetail:
    run = await store.run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"planning run {run_id} not found")
    return PlanningRunDetail(run=run, lines=await store.lines(run_id))


@router.post("/runs/{run_id}/what-if", response_model=WhatIfResponse)
async def what_if(
    run_id: str,
    body: WhatIfRequest,
    _: Principal = Approver,
    store: PlanningReadStore = Injected(PlanningReadStore),  # type: ignore[type-abstract]
    agents: Agents = Injected(Agents),
) -> WhatIfResponse:
    """Recompute one line with other parameters; nothing is written anywhere."""
    run = await store.run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"planning run {run_id} not found")
    baseline = next(
        (r.line for r in await store.lines(run_id) if r.line.line_id == body.line_id), None
    )
    if baseline is None:
        raise HTTPException(status_code=404, detail=f"line {body.line_id} not in run {run_id}")
    task = InventoryPlanningTask(
        kind="what_if",
        case_id=new_id("whatif"),
        product_ids=[baseline.product_id],
        as_of=run.as_of,
        overrides=body.overrides,
    )
    try:
        reply = await agents.for_name("inventory_planning").send(
            task.model_dump_json(), case_id=task.case_id
        )
        result = InventoryPlanningResult.model_validate_json(reply.text)
    except (ScError, ValidationError, LookupError) as exc:
        logger.warning("what-if failed: {}", exc)
        raise HTTPException(status_code=502, detail="the planner could not simulate") from exc
    if result.proposal is None or not result.proposal.lines:
        raise HTTPException(status_code=502, detail=result.outcome.summary)
    return WhatIfResponse(
        baseline=baseline, simulated=result.proposal.lines[0], run_id=result.run_id
    )
