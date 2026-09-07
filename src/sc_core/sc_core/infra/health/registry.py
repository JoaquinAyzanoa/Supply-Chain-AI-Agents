"""Registry of readiness checks.

Each service registers the dependencies it needs (Odoo, Graph, Postgres,
Redis, LLM provider) and ``/health/ready`` runs them all concurrently. A
critical failure makes the endpoint return 503 so orchestrators stop routing
traffic; a non-critical failure is reported as ``degraded`` with HTTP 200.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from sc_core.infra.health.types import (
    CheckResult,
    CheckStatus,
    HealthCheck,
    HealthReport,
    OverallStatus,
)


@dataclass(frozen=True)
class _Registered:
    name: str
    check: HealthCheck
    timeout: float
    critical: bool


class HealthRegistry:
    """Named checks with per-check timeouts."""

    def __init__(self) -> None:
        self._checks: dict[str, _Registered] = {}

    def register(
        self,
        name: str,
        check: HealthCheck,
        *,
        timeout: float = 5.0,
        critical: bool = True,
    ) -> None:
        """Add a check. Re-registering a name replaces the previous check."""
        if not name:
            raise ValueError("health check name must not be empty")
        if timeout <= 0:
            raise ValueError("health check timeout must be positive")
        self._checks[name] = _Registered(name, check, timeout, critical)

    def names(self) -> list[str]:
        return list(self._checks)

    async def run(self, *, service: str, version: str) -> HealthReport:
        """Run every check concurrently and aggregate the result."""
        results = await asyncio.gather(*(self._run_one(r) for r in self._checks.values()))
        failed = [r for r in results if r.status is CheckStatus.FAIL]
        if any(r.critical for r in failed):
            status = OverallStatus.FAIL
        elif failed:
            status = OverallStatus.DEGRADED
        else:
            status = OverallStatus.OK
        return HealthReport(status=status, service=service, version=version, checks=list(results))

    @staticmethod
    async def _run_one(reg: _Registered) -> CheckResult:
        started = time.perf_counter()
        detail: str | None = None
        status = CheckStatus.OK
        try:
            async with asyncio.timeout(reg.timeout):
                await reg.check()
        except TimeoutError:
            status, detail = CheckStatus.FAIL, f"timeout after {reg.timeout:g}s"
        except Exception as exc:  # noqa: BLE001 - a check may raise anything
            status, detail = CheckStatus.FAIL, f"{type(exc).__name__}: {exc}"
        return CheckResult(
            name=reg.name,
            status=status,
            critical=reg.critical,
            duration_ms=(time.perf_counter() - started) * 1000,
            detail=detail,
        )
