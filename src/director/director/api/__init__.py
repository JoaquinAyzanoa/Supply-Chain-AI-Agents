"""The Control Tower API, mounted at ``/api``.

Every router here is JSON, JWT-protected (``auth``) and tagged for the
OpenAPI document the frontend client is generated from.
"""

from __future__ import annotations

from fastapi import APIRouter

from director.api import (
    ai,
    approvals,
    assistant,
    auth,
    autonomy,
    board,
    briefing,
    calendar,
    cases,
    chat,
    demo,
    exceptions,
    home,
    learning,
    mailbox,
    performance,
    planning,
    playbooks,
    push,
    risk,
    runs,
    settings,
    sourcing,
    stream,
    suppliers,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(ai.router)
api_router.include_router(approvals.router)
api_router.include_router(assistant.router)
api_router.include_router(autonomy.router)
api_router.include_router(learning.router)
api_router.include_router(board.router)
api_router.include_router(briefing.router)
api_router.include_router(mailbox.router)
api_router.include_router(performance.router)
api_router.include_router(cases.router)
api_router.include_router(chat.router)
api_router.include_router(demo.router)
api_router.include_router(exceptions.router)
api_router.include_router(home.router)
api_router.include_router(planning.router)
api_router.include_router(calendar.router)
api_router.include_router(risk.router)
api_router.include_router(playbooks.router)
api_router.include_router(push.router)
api_router.include_router(runs.router)
api_router.include_router(settings.router)
api_router.include_router(sourcing.router)
api_router.include_router(stream.router)
api_router.include_router(suppliers.router)

__all__ = ["api_router"]
