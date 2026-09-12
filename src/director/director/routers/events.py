"""``POST /events``: accept signed events from mail_sync, the scheduler, Odoo and agents.

The signature is verified, the event parsed and stored in the inbox
(idempotent by ``event_id``), and the producer gets 202 at once. A newly
stored event is then handed to the orchestration workflow in a background
task, so no producer ever waits for a model run. An unknown event type is a
422 (the parser rejects it), never a crash.

``POST /jobs/{job}``: the scheduler's director jobs (follow-ups, planning,
performance). The tick is stored like any event and run through the
workflow *before* answering, so the scheduler's run record carries the
job's summary and a failure shows up as one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import ValidationError

from director.inbox import EventInbox
from director.workflow import Orchestrator
from sc_core.a2a.events import SignedBody
from sc_core.schema.base import StrictModel
from sc_core.schema.events import ScheduledTick, parse_event

router = APIRouter(tags=["events"])

JOB_NAMES = {"po-followups", "inventory-planning", "supplier-performance"}


class Accepted(StrictModel):
    accepted: bool
    duplicate: bool = False
    event_id: str
    event_type: str
    dispatched: bool = False
    result: dict[str, Any] | None = None


@router.post("/events", status_code=202, response_model=Accepted)
async def receive_event(
    background: BackgroundTasks,
    body: bytes = SignedBody,
    inbox: EventInbox = Injected(EventInbox),  # type: ignore[type-abstract]
    orchestrator: Orchestrator = Injected(Orchestrator),
) -> Accepted:
    try:
        event = parse_event(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    stored = await inbox.store(event)
    logger.bind(event_id=event.event_id, event_type=event.type, case_id=event.case_id).info(
        "event {}", "accepted" if stored else "duplicate"
    )
    dispatched = False
    if stored:
        background.add_task(orchestrator.handle, event)
        dispatched = True
    return Accepted(
        accepted=True,
        duplicate=not stored,
        event_id=event.event_id,
        event_type=event.type,
        dispatched=dispatched,
    )


@router.post("/jobs/{job}", status_code=202, response_model=Accepted)
async def receive_job(
    job: str,
    body: bytes = SignedBody,
    inbox: EventInbox = Injected(EventInbox),  # type: ignore[type-abstract]
    orchestrator: Orchestrator = Injected(Orchestrator),
) -> Accepted:
    if job not in JOB_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown job {job!r}")
    try:
        tick = ScheduledTick.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    stored = await inbox.store(tick)
    if not stored:
        logger.bind(job=job, run_id=tick.run_id).info("job tick already handled")
        return Accepted(accepted=True, duplicate=True, event_id=tick.event_id, event_type=tick.type)
    result = await orchestrator.handle(tick)
    return Accepted(
        accepted=True,
        event_id=tick.event_id,
        event_type=tick.type,
        dispatched=True,
        result=result,
    )
