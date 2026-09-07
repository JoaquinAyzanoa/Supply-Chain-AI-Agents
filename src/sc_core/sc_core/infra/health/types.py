"""Types for health checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel, Field

# A check is an async callable that returns normally when healthy and raises
# (any exception) when not. Keeping the contract this small means a check is
# usually a one-liner: ``async def odoo(): await client.version()``.
HealthCheck = Callable[[], Awaitable[None]]


class CheckStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"


class OverallStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"  # only non-critical checks failed
    FAIL = "fail"  # at least one critical check failed


class CheckResult(BaseModel):
    name: str
    status: CheckStatus
    critical: bool
    duration_ms: float = Field(ge=0)
    detail: str | None = None


class HealthReport(BaseModel):
    status: OverallStatus
    service: str
    version: str
    checks: list[CheckResult]

    @property
    def http_status(self) -> int:
        return 503 if self.status is OverallStatus.FAIL else 200
