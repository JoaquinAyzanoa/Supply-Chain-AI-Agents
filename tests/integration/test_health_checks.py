"""Infrastructure health checks against real containers."""

import pytest

from sc_core.infra.health import CheckStatus, HealthRegistry, OverallStatus, checks

pytestmark = pytest.mark.integration


async def test_postgres_and_redis_checks_pass(postgres_dsn: str, redis_dsn: str) -> None:
    reg = HealthRegistry()
    reg.register("postgres", checks.postgres(postgres_dsn))
    reg.register("redis", checks.redis(redis_dsn))
    report = await reg.run(service="t", version="1")
    assert report.status is OverallStatus.OK, report.model_dump()


async def test_unreachable_services_fail_fast() -> None:
    reg = HealthRegistry()
    reg.register(
        "postgres", checks.postgres("postgresql://app:app@127.0.0.1:1/app", connect_timeout=1)
    )
    reg.register("redis", checks.redis("redis://127.0.0.1:1/0", timeout=1), critical=False)
    report = await reg.run(service="t", version="1")
    assert report.status is OverallStatus.FAIL
    assert all(c.status is CheckStatus.FAIL for c in report.checks)
    assert all(c.duration_ms < 5000 for c in report.checks)
