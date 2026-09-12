"""Scheduler ticks: the job id says which in-process job runs.

The jobs themselves live next to the workflow (follow-ups in phase 6,
planning in phase 7, supplier performance in phase 9); the router only names
them so the table stays complete and testable.
"""

from __future__ import annotations

from director.router import Route
from director.store import CaseKind
from sc_core.schema.events import BaseEvent, ScheduledTick

JOB_KINDS: dict[str, CaseKind] = {
    "po_followups": "eta",
    "inventory_planning": "planning",
    "supplier_performance": "receipt",
}


def dispatch(event: BaseEvent) -> Route:
    assert isinstance(event, ScheduledTick)
    kind = JOB_KINDS.get(event.job_id)
    if kind is None:
        return Route(case_kind="planning", note=f"unknown scheduler job {event.job_id!r}")
    return Route(case_kind=kind, job=event.job_id)
