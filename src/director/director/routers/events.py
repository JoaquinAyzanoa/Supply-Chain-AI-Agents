"""``POST /events``: accept signed events from mail_sync, scheduler and Odoo.

The signature is verified, the event parsed and stored in the inbox
(idempotent by ``event_id``), and the producer gets 202 at once. A newly
stored mail event is then handed to the supplier communications agent in a
background task, so mail_sync never waits for a model run. Phase 6 replaces
the dispatcher with the routing table; the contract stays.

``POST /jobs/{job}``: placeholder targets for the scheduler's director jobs
(follow-ups, planning, performance) until their agents exist. They validate
the signature and record the tick so the scheduler sees a clean run.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import ValidationError

from director.dispatch import Dispatcher
from director.inbox import EventInbox
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


@router.post("/events", status_code=202, response_model=Accepted)
async def receive_event(
    background: BackgroundTasks,
    body: bytes = SignedBody,
    inbox: EventInbox = Injected(EventInbox),  # type: ignore[type-abstract]
    dispatcher: Dispatcher = Injected(Dispatcher),
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
    if stored and event.type.startswith("inbound_mail."):
        background.add_task(dispatcher.dispatch, event)
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
) -> Accepted:
    if job not in JOB_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown job {job!r}")
    try:
        tick = ScheduledTick.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    stored = await inbox.store(tick)
    logger.bind(job=job, run_id=tick.run_id).info("job tick recorded; handler arrives in phase 6")
    return Accepted(
        accepted=True, duplicate=not stored, event_id=tick.event_id, event_type=tick.type
    )
