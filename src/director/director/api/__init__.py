"""The Control Tower API, mounted at ``/api``.

Every router here is JSON, JWT-protected (``auth``) and tagged for the
OpenAPI document the frontend client is generated from.
"""

from __future__ import annotations

from fastapi import APIRouter

from director.api import (
    approvals,
    auth,
    autonomy,
    board,
    cases,
    chat,
    exceptions,
    learning,
    mailbox,
    performance,
    planning,
    runs,
    settings,
    stream,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(approvals.router)
api_router.include_router(autonomy.router)
api_router.include_router(learning.router)
api_router.include_router(board.router)
api_router.include_router(mailbox.router)
api_router.include_router(performance.router)
api_router.include_router(cases.router)
api_router.include_router(chat.router)
api_router.include_router(exceptions.router)
api_router.include_router(planning.router)
api_router.include_router(runs.router)
api_router.include_router(settings.router)
api_router.include_router(stream.router)

__all__ = ["api_router"]
