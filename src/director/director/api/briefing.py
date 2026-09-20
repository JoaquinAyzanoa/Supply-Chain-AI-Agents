"""The morning briefing in the Control Tower: read it, build it now, email it."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.auth import Approver, Principal, Viewer
from director.desk.briefing import Briefing, BriefingJob, BriefingStore
from sc_core.schema.base import StrictModel

router = APIRouter(prefix="/briefing", tags=["briefing"])


class EmailRequest(StrictModel):
    to: list[str] = Field(
        default_factory=list, description="recipients; empty means the ones in Settings"
    )


class EmailResponse(StrictModel):
    day: date
    sent_to: list[str]


@router.get("", response_model=Briefing)
async def read_briefing(
    day: date | None = Query(default=None, description="a past day; default: the latest"),
    _: Principal = Viewer,
    store: BriefingStore = Injected(BriefingStore),  # type: ignore[type-abstract]
) -> Briefing:
    briefing = await store.get(day) if day else await store.latest()
    if briefing is None:
        raise HTTPException(status_code=404, detail="no briefing yet")
    return briefing


@router.get("/history", response_model=list[Briefing])
async def history(
    limit: int = Query(default=14, ge=1, le=60),
    _: Principal = Viewer,
    store: BriefingStore = Injected(BriefingStore),  # type: ignore[type-abstract]
) -> list[Briefing]:
    return await store.recent(limit=limit)


@router.post("/run", response_model=Briefing, status_code=201)
async def run_now(
    principal: Principal = Approver,
    job: BriefingJob = Injected(BriefingJob),
) -> Briefing:
    """Build today's briefing now (the 07:30 job does the same)."""
    briefing = await job.build_and_save()
    logger.bind(by=principal.email, day=briefing.day.isoformat()).info("briefing built by hand")
    return briefing


@router.post("/email", response_model=EmailResponse)
async def email_briefing(
    body: EmailRequest,
    principal: Principal = Approver,
    job: BriefingJob = Injected(BriefingJob),
    store: BriefingStore = Injected(BriefingStore),  # type: ignore[type-abstract]
) -> EmailResponse:
    briefing = await store.latest()
    if briefing is None:
        raise HTTPException(status_code=404, detail="no briefing yet")
    recipients = body.to or await job.recipients()
    if not recipients:
        raise HTTPException(
            status_code=422, detail="no recipients: give addresses or set them in Settings"
        )
    sent = await job.email(briefing, recipients)
    if not sent:
        raise HTTPException(status_code=502, detail="the mailbox did not send the briefing")
    logger.bind(by=principal.email, to=len(sent)).info("briefing emailed by hand")
    return EmailResponse(day=briefing.day, sent_to=sent)
