"""Tests for sc_core.infra.health."""

import asyncio

import pytest

from sc_core.infra.health import CheckStatus, HealthRegistry, OverallStatus


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise ConnectionError("refused")


async def _slow() -> None:
    await asyncio.sleep(1)


async def test_all_ok() -> None:
    reg = HealthRegistry()
    reg.register("a", _ok)
    reg.register("b", _ok, critical=False)
    report = await reg.run(service="svc", version="1")
    assert report.status is OverallStatus.OK
    assert report.http_status == 200
    assert [c.name for c in report.checks] == ["a", "b"]
    assert all(c.status is CheckStatus.OK for c in report.checks)


async def test_critical_failure_is_fail_with_detail() -> None:
    reg = HealthRegistry()
    reg.register("odoo", _boom)
    report = await reg.run(service="svc", version="1")
    assert report.status is OverallStatus.FAIL
    assert report.http_status == 503
    (check,) = report.checks
    assert check.status is CheckStatus.FAIL
    assert check.detail == "ConnectionError: refused"


async def test_non_critical_failure_is_degraded() -> None:
    reg = HealthRegistry()
    reg.register("core", _ok)
    reg.register("llm", _boom, critical=False)
    report = await reg.run(service="svc", version="1")
    assert report.status is OverallStatus.DEGRADED
    assert report.http_status == 200


async def test_timeout_is_reported() -> None:
    reg = HealthRegistry()
    reg.register("slow", _slow, timeout=0.05)
    report = await reg.run(service="svc", version="1")
    (check,) = report.checks
    assert check.status is CheckStatus.FAIL
    assert check.detail == "timeout after 0.05s"
    assert check.duration_ms < 500


async def test_checks_run_concurrently() -> None:
    reg = HealthRegistry()

    async def wait() -> None:
        await asyncio.sleep(0.1)

    for i in range(5):
        reg.register(f"c{i}", wait)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await reg.run(service="svc", version="1")
    assert loop.time() - started < 0.4  # not 5 x 0.1


def test_register_validates_arguments() -> None:
    reg = HealthRegistry()
    with pytest.raises(ValueError):
        reg.register("", _ok)
    with pytest.raises(ValueError):
        reg.register("x", _ok, timeout=0)


def test_reregister_replaces() -> None:
    reg = HealthRegistry()
    reg.register("x", _ok)
    reg.register("x", _boom)
    assert reg.names() == ["x"]
