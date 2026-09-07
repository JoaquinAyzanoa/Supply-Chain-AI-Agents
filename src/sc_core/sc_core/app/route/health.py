"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi_injector import Injected

from sc_core import __meta__
from sc_core.infra.health import HealthRegistry, HealthReport
from sc_core.infra.settings import Settings
from sc_core.schema.system import LiveResponse

router = APIRouter(prefix="/health", tags=["system"])


@router.get("/live", response_model=LiveResponse)
async def live(settings: Settings = Injected(Settings)) -> LiveResponse:
    """The process is up. Never checks dependencies."""
    return LiveResponse(service=settings.service_name, version=__meta__.__version__)


@router.get(
    "/ready",
    response_model=HealthReport,
    responses={503: {"model": HealthReport, "description": "a critical dependency is down"}},
)
async def ready(
    response: Response,
    settings: Settings = Injected(Settings),
    registry: HealthRegistry = Injected(HealthRegistry),
) -> HealthReport:
    """All registered checks. 503 when a critical one fails."""
    report = await registry.run(service=settings.service_name, version=__meta__.__version__)
    response.status_code = report.http_status
    return report
