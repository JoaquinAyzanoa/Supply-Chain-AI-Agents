"""In-process jobs the scheduler triggers through the director.

A ``ScheduledTick`` routes to a job by id; the runner executes it under the
tick's case (the run id) and returns a small JSON summary that lands on the
inbox row. Follow-ups arrive in P6-S5, planning in phase 7, supplier
performance in phase 9.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from loguru import logger

from sc_core.schema.events import ScheduledTick


@runtime_checkable
class JobRunner(Protocol):
    async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]: ...


class NoJobs:
    """Every job is a recorded no-op (the phase that implements it replaces this)."""

    async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]:
        logger.bind(job=job_id, run_id=tick.run_id).info("job not implemented yet")
        return {"job": job_id, "status": "not_implemented"}
