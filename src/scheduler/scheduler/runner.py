"""APScheduler wrapper: cron firings and manual runs share one dispatch path.

A dispatch POSTs a signed ``ScheduledTick`` to the job's target and records
the outcome. Overlap policy ``skip``: a firing while the same job is still
running is recorded as ``skipped_overlap`` and not sent; the targets hold
their own locks too (mail_sync on Redis), so this is a first line, not the
only one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from sc_core.a2a.events import EVENT_ID_HEADER, EVENT_TYPE_HEADER, HmacSigner, encode_event
from sc_core.infra import tracing
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import NotFound
from sc_core.shared.idempotency import new_id
from sc_core.shared.time import utc_now
from scheduler.jobs import Job
from scheduler.runs import RunRecord, RunStore, Trigger

SOURCE = "scheduler"


class JobRunner:
    def __init__(
        self,
        jobs: list[Job],
        *,
        signer: HmacSigner,
        runs: RunStore,
        timezone: str,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.jobs = {job.id: job for job in jobs}
        self._signer = signer
        self._runs = runs
        self._timezone = timezone
        self._http = http or httpx.AsyncClient()
        self._clock = clock
        self._running: set[str] = set()
        self._scheduler: AsyncIOScheduler | None = None

    # --- lifecycle ------------------------------------------------------------------

    def start(self) -> None:
        scheduler = AsyncIOScheduler(timezone=self._timezone)
        for job in self.jobs.values():
            scheduler.add_job(
                self._fire,
                trigger=job.trigger(self._timezone),
                id=job.id,
                args=[job.id],
                coalesce=True,  # several missed firings collapse into one
                misfire_grace_time=300,
                max_instances=1,
            )
        scheduler.start()
        self._scheduler = scheduler
        logger.info("scheduler started with {} job(s)", len(self.jobs))

    async def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        await self._http.aclose()

    def next_fire_times(self) -> dict[str, datetime | None]:
        if self._scheduler is None:
            return dict.fromkeys(self.jobs)
        return {
            job_id: getattr(self._scheduler.get_job(job_id), "next_run_time", None)
            for job_id in self.jobs
        }

    # --- dispatch -------------------------------------------------------------------

    def job(self, job_id: str) -> Job:
        try:
            return self.jobs[job_id]
        except KeyError:
            raise NotFound(f"unknown job {job_id!r}", details={"jobs": sorted(self.jobs)}) from None

    async def run_now(self, job_id: str) -> RunRecord:
        return await self.dispatch(self.job(job_id), trigger="manual")

    async def _fire(self, job_id: str) -> None:
        await self.dispatch(self.jobs[job_id], trigger="cron")

    async def dispatch(self, job: Job, *, trigger: Trigger) -> RunRecord:
        now = self._clock()
        record = RunRecord(run_id=new_id("run"), job_id=job.id, trigger=trigger, started_at=now)
        if job.id in self._running:
            record.status, record.finished_at = "skipped_overlap", now
            record.summary = "previous run still active"
            await self._runs.save(record)
            logger.bind(job=job.id).warning("job skipped: previous run still active")
            return record

        self._running.add(job.id)
        try:
            with tracing.start_case(record.run_id, f"job.{job.id}", input={"trigger": trigger}):
                tick = ScheduledTick(
                    source=SOURCE,
                    case_id=record.run_id,
                    trace_id=tracing.current_trace_id(),
                    job_id=job.id,
                    run_id=record.run_id,
                    scheduled_at=now,
                    trigger=trigger,
                )
                await self._runs.save(record)
                await self._post(job, tick, record)
        finally:
            self._running.discard(job.id)
            record.finished_at = self._clock()
            await self._runs.save(record)
        logger.bind(job=job.id, run_id=record.run_id, status=record.status).info("job finished")
        return record

    async def _post(self, job: Job, tick: ScheduledTick, record: RunRecord) -> None:
        body = encode_event(tick)
        headers = {
            "Content-Type": "application/json",
            "X-SC-Signature": self._signer.sign(body),
            EVENT_TYPE_HEADER: tick.type,
            EVENT_ID_HEADER: tick.event_id,
        }
        try:
            response = await self._http.post(
                job.target, content=body, headers=headers, timeout=job.timeout_seconds
            )
        except httpx.HTTPError as exc:
            record.status, record.summary = "failed", f"{type(exc).__name__}: {exc}"[:500]
            return
        record.http_status = response.status_code
        record.summary = response.text[:500]
        record.status = "ok" if response.is_success else "failed"


async def wait_for(condition: Callable[[], bool], *, timeout: float = 5.0) -> bool:
    """Test helper: poll ``condition`` until true or ``timeout`` elapses."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return condition()
