"""Scheduler ticks: the job id says which in-process job runs.

The jobs themselves live next to the workflow (follow-ups in phase 6,
planning in phase 7, supplier performance in phase 9); the router only names
them so the table stays complete and testable.
"""

from __future__ import annotations

from typing import Any

from director.router import Route
from director.store import CaseKind
from sc_core.schema.events import BaseEvent, ScheduledTick

JOB_KINDS: dict[str, CaseKind] = {
    "po_followups": "eta",
    "inventory_planning": "planning",
    "supplier_performance": "receipt",
    "calibration": "planning",  # no case of its own; the suggestions land on the Autonomy page
    "playbooks": "eta",  # the hourly nudge of every active playbook run
}


def dispatch(event: BaseEvent) -> Route:
    assert isinstance(event, ScheduledTick)
    kind = JOB_KINDS.get(event.job_id)
    if kind is None:
        return Route(case_kind="planning", note=f"unknown scheduler job {event.job_id!r}")
    return Route(case_kind=kind, job=event.job_id)


class PlaybooksJob:
    """The hourly tick: every active playbook run gets a chance to move."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]:
        if job_id != "playbooks":
            return {"job": job_id, "status": "not_implemented"}
        result = await self._engine.tick()
        return {"job": job_id, "status": "ok", **result}
