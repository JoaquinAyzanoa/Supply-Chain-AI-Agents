"""The demand calendar: promotions, holidays and projects people know about."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger

from director.api.auth import Approver, Principal, Viewer
from sc_core.infra.calendar import CalendarStore
from sc_core.schema.calendar import CalendarEvent
from sc_core.shared.errors import NotFound

router = APIRouter(prefix="/planning/calendar", tags=["planning"])


@router.get("", response_model=list[CalendarEvent])
async def list_events(
    since: date | None = Query(default=None, description="events ending on or after this day"),
    _: Principal = Viewer,
    store: CalendarStore = Injected(CalendarStore),  # type: ignore[type-abstract]
) -> list[CalendarEvent]:
    return await store.events(since=since)


@router.post("", response_model=CalendarEvent, status_code=201)
async def add_event(
    body: CalendarEvent,
    principal: Principal = Approver,
    store: CalendarStore = Injected(CalendarStore),  # type: ignore[type-abstract]
) -> CalendarEvent:
    saved = await store.add(body.model_copy(update={"id": None, "created_by": principal.email}))
    logger.bind(event_id=saved.id, kind=saved.kind, by=principal.email).info("calendar event added")
    return saved


@router.delete("/{event_id}", status_code=204)
async def remove_event(
    event_id: int,
    principal: Principal = Approver,
    store: CalendarStore = Injected(CalendarStore),  # type: ignore[type-abstract]
) -> None:
    try:
        await store.remove(event_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    logger.bind(event_id=event_id, by=principal.email).info("calendar event removed")
