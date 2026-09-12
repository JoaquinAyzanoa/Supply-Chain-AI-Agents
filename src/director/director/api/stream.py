"""``GET /api/stream``: Server-Sent Events for the Control Tower.

Browsers cannot set headers on an ``EventSource``, so this route takes the
session token as a query parameter. Frames are the realtime events
(``case_updated``, ``approval_created``, ``approval_resolved``,
``run_finished``, ``settings_changed``) plus a comment heartbeat while
idle; the UI refetches on each event instead of polling.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from fastapi_injector import Injected

from director.api.auth import decode_token, jwt_secret
from sc_core.app.realtime import Realtime, sse_frames
from sc_core.infra.settings import Settings

router = APIRouter(tags=["stream"])


@router.get("/stream", responses={200: {"content": {"text/event-stream": {}}}})
async def stream(
    token: str = Query(description="the session token (EventSource cannot send headers)"),
    settings: Settings = Injected(Settings),
    realtime: Realtime = Injected(Realtime),  # type: ignore[type-abstract]
) -> StreamingResponse:
    decode_token(token, secret=jwt_secret(settings))  # any signed-in role may listen
    return StreamingResponse(
        sse_frames(realtime, heartbeat_seconds=settings.ui.sse_heartbeat_seconds),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
