"""The Control Tower API, mounted at ``/api``.

Every router here is JSON, JWT-protected (``auth``) and tagged for the
OpenAPI document the frontend client is generated from.
"""

from __future__ import annotations

from fastapi import APIRouter

from director.api import approvals, auth, settings

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(approvals.router)
api_router.include_router(settings.router)

__all__ = ["api_router"]
