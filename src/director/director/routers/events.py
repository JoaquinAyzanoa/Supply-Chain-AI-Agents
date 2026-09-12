"""``POST /events``: accept signed events from mail_sync, scheduler and Odoo.

Phase 4 stub: verify the signature, parse the event, store it in the inbox
and answer 202. Phase 6 replaces the handler body with routing to agents;
the contract (signature, 202, idempotent by ``event_id``) stays.

``POST /jobs/{job}``: placeholder targets for the scheduler's director jobs
(follow-ups, planning, performance) until their agents exist. They validate
the signature and record the tick so the scheduler sees a clean run.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import ValidationError

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


@router.post("/events", status_code=202, response_model=Accepted)
async def receive_event(
    body: bytes = SignedBody,
    inbox: EventInbox = Injected(EventInbox),  # type: ignore[type-abstract]
) -> Accepted:
    try:
        event = parse_event(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    stored = await inbox.store(event)
    logger.bind(event_id=event.event_id, event_type=event.type, case_id=event.case_id).info(
        "event {}", "accepted" if stored else "duplicate"
    )
    return Accepted(
        accepted=True, duplicate=not stored, event_id=event.event_id, event_type=event.type
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
