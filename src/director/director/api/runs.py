"""Runs: what the agents executed and what the scheduler fired.

Agent runs come from Odoo's ``sc.agent.run`` (status, model, tokens, cost,
duration, trace link); this is where model comparisons are read day to
day. Scheduler runs come from ``scheduler_runs`` in the application
database, with each job's cron expression so the UI can say when it fires.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, Query
from fastapi_injector import Injected

from director.api.auth import Principal, Viewer
from sc_core.infra import tracing
from sc_core.infra.db import Database
from sc_core.infra.settings import Settings
from sc_core.odoo.models import AgentRun
from sc_core.schema.base import StrictModel

router = APIRouter(prefix="/runs", tags=["runs"])


class AgentRunView(StrictModel):
    run_id: str
    agent: str
    case_id: str | None = None
    po_id: int | None = None
    po_name: str | None = None
    status: str
    model: str | None = None
    summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    trace_url: str | None = None


class SchedulerRunView(StrictModel):
    run_id: str
    job_id: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    http_status: int | None = None
    summary: str | None = None
    cron: str | None = None


@runtime_checkable
class RunsGateway(Protocol):
    async def recent(
        self,
        *,
        agent: str | None = None,
        model: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AgentRun]: ...

    async def for_cases(self, case_ids: Sequence[str]) -> list[AgentRun]: ...


@runtime_checkable
class SchedulerRuns(Protocol):
    async def recent(
        self, *, job_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...


class PostgresSchedulerRuns:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def recent(self, *, job_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if job_id:
            return await self._db.fetch_all(
                "SELECT * FROM scheduler_runs WHERE job_id = %s ORDER BY started_at DESC LIMIT %s",
                (job_id, limit),
            )
        return await self._db.fetch_all(
            "SELECT * FROM scheduler_runs ORDER BY started_at DESC LIMIT %s", (limit,)
        )


def run_view(run: AgentRun) -> AgentRunView:
    duration = None
    if run.started_at and run.finished_at:
        duration = round((run.finished_at - run.started_at).total_seconds(), 1)
    return AgentRunView(
        run_id=run.run_id,
        agent=run.agent,
        case_id=run.case_id,
        po_id=run.po_id.id if run.po_id else None,
        po_name=run.po_id.name if run.po_id else None,
        status=run.status,
        model=run.model,
        summary=run.summary,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=duration,
        llm_calls=run.llm_calls,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cost_usd=round(run.cost_usd, 6),
        trace_url=tracing.browser_trace_url(run.trace_url),
    )


def cron_for(settings: Settings, job_id: str) -> str | None:
    return getattr(settings.scheduler, f"{job_id}_cron", None)


@router.get("", response_model=list[AgentRunView])
async def list_runs(
    agent: str | None = Query(default=None),
    model: str | None = Query(default=None),
    status: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: Principal = Viewer,
    runs: RunsGateway = Injected(RunsGateway),  # type: ignore[type-abstract]
) -> list[AgentRunView]:
    rows = await runs.recent(agent=agent, model=model, status=status, since=since, limit=limit)
    return [run_view(r) for r in rows]


@router.get("/scheduler", response_model=list[SchedulerRunView])
async def list_scheduler_runs(
    job: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    _: Principal = Viewer,
    store: SchedulerRuns = Injected(SchedulerRuns),  # type: ignore[type-abstract]
    settings: Settings = Injected(Settings),
) -> list[SchedulerRunView]:
    rows = await store.recent(job_id=job, limit=limit)
    return [
        SchedulerRunView(
            run_id=str(r["run_id"]),
            job_id=str(r["job_id"]),
            trigger=str(r["trigger"]),
            started_at=r["started_at"],
            finished_at=r.get("finished_at"),
            status=str(r["status"]),
            http_status=r.get("http_status"),
            summary=r.get("summary"),
            cron=cron_for(settings, str(r["job_id"])),
        )
        for r in rows
    ]
