"""ASGI entrypoint for the scheduler.

``GET /jobs`` lists the table with next fire times and the last run;
``POST /jobs/{id}/run-now`` dispatches immediately and returns the run
record (this is what ``just sync-now`` calls).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, FastAPI
from fastapi_injector import Injected
from injector import Module, provider, singleton
from loguru import logger

from sc_core.a2a.events import HmacSigner
from sc_core.app import create_application
from sc_core.infra.db import Database
from sc_core.infra.module import DbModule
from sc_core.infra.settings import Settings
from sc_core.schema.base import StrictModel
from scheduler import __version__
from scheduler.jobs import job_table
from scheduler.runner import JobRunner
from scheduler.runs import PostgresRunStore, RunRecord, RunStore

settings = Settings(service_name="scheduler")


class SchedulerModule(Module):
    @provider
    @singleton
    def provide_runs(self, db: Database) -> RunStore:  # type: ignore[type-abstract]
        return PostgresRunStore(db)

    @provider
    @singleton
    def provide_runner(self, settings: Settings, runs: RunStore) -> JobRunner:  # type: ignore[type-abstract]
        return JobRunner(
            job_table(settings),
            signer=HmacSigner(settings.events.signing_secret.get_secret_value()),
            runs=runs,
            timezone=settings.timezone,
        )


class JobView(StrictModel):
    id: str
    cron: str
    target: str
    timeout_seconds: float
    next_fire_at: datetime | None
    last_run: RunRecord | None


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobView])
async def list_jobs(
    runner: JobRunner = Injected(JobRunner),
    runs: RunStore = Injected(RunStore),  # type: ignore[type-abstract]
) -> list[JobView]:
    next_times = runner.next_fire_times()
    return [
        JobView(
            id=job.id,
            cron=job.cron,
            target=job.target,
            timeout_seconds=job.timeout_seconds,
            next_fire_at=next_times.get(job.id),
            last_run=await runs.last(job.id),
        )
        for job in runner.jobs.values()
    ]


@router.post("/{job_id}/run-now", response_model=RunRecord)
async def run_now(job_id: str, runner: JobRunner = Injected(JobRunner)) -> RunRecord:
    return await runner.run_now(job_id)


def build_app() -> FastAPI:
    return create_application(
        settings,
        version=__version__,
        routers=[router],
        modules=[DbModule(), SchedulerModule()],
        startup=[_startup],
        shutdown=[_shutdown],
    )


async def _startup() -> None:
    injector = app.state.injector
    await injector.get(Database).open()
    injector.get(JobRunner).start()


async def _shutdown() -> None:
    injector = app.state.injector
    await injector.get(JobRunner).stop()
    await injector.get(Database).close()


if not settings.events.configured:
    logger.warning("SC__EVENTS__SIGNING_SECRET not set; the scheduler cannot sign dispatches")

app = build_app()
