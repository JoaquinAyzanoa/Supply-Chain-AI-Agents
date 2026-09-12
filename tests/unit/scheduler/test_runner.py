"""JobRunner: signed dispatch, outcome recording, overlap policy, lifecycle."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from sc_core.a2a.events import EVENT_ID_HEADER, SIGNATURE_HEADER, HmacSigner
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import NotFound
from scheduler.jobs import Job
from scheduler.runner import JobRunner
from scheduler.runs import MemoryRunStore

SECRET = "sched-secret"


class Target:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.delay = 0.0
        self.fail: Exception | None = None

    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.fail:
                raise self.fail
            if self.delay:
                await asyncio.sleep(self.delay)
            return httpx.Response(self.status, json={"linked": 1})

        return httpx.MockTransport(handler)


def _jobs() -> list[Job]:
    return [
        Job(id="mail_sync", cron="*/30 * * * *", target="http://mail_sync.test/jobs/sync"),
        Job(id="po_followups", cron="0 9 * * 1-5", target="http://director.test/jobs/po-followups"),
    ]


@pytest.fixture
def target() -> Target:
    return Target()


@pytest.fixture
def runs() -> MemoryRunStore:
    return MemoryRunStore()


@pytest.fixture
def runner(target: Target, runs: MemoryRunStore) -> JobRunner:
    return JobRunner(
        _jobs(),
        signer=HmacSigner(SECRET),
        runs=runs,
        timezone="America/Lima",
        http=httpx.AsyncClient(transport=target.transport()),
        clock=lambda: datetime(2026, 9, 12, 15, 0, tzinfo=UTC),
    )


async def test_run_now_posts_signed_tick_and_records_ok(
    runner: JobRunner, target: Target, runs: MemoryRunStore
) -> None:
    record = await runner.run_now("mail_sync")
    assert record.status == "ok" and record.http_status == 200 and record.trigger == "manual"
    assert record.finished_at is not None and '"linked":1' in (record.summary or "")

    request = target.requests[0]
    assert str(request.url) == "http://mail_sync.test/jobs/sync"
    assert HmacSigner(SECRET).verify(request.content, request.headers[SIGNATURE_HEADER])
    tick = ScheduledTick.model_validate_json(request.content)
    assert tick.job_id == "mail_sync" and tick.run_id == record.run_id
    assert tick.case_id == record.run_id and tick.trigger == "manual"
    assert request.headers[EVENT_ID_HEADER] == tick.event_id
    assert runs.statuses("mail_sync") == ["ok"]


async def test_http_error_and_transport_error_are_recorded_as_failed(
    runner: JobRunner, target: Target, runs: MemoryRunStore
) -> None:
    target.status = 503
    failed = await runner.run_now("po_followups")
    assert failed.status == "failed" and failed.http_status == 503

    target.fail = httpx.ConnectError("refused")
    down = await runner.run_now("po_followups")
    assert down.status == "failed" and down.http_status is None
    assert "ConnectError" in (down.summary or "")
    assert runs.statuses("po_followups") == ["failed", "failed"]


async def test_overlap_is_skipped_while_previous_run_active(
    runner: JobRunner, target: Target, runs: MemoryRunStore
) -> None:
    target.delay = 0.2
    first, second = await asyncio.gather(runner.run_now("mail_sync"), runner.run_now("mail_sync"))
    statuses = sorted([first.status, second.status])
    assert statuses == ["ok", "skipped_overlap"]
    assert len(target.requests) == 1
    skipped = first if first.status == "skipped_overlap" else second
    assert skipped.summary == "previous run still active"


async def test_unknown_job_is_not_found(runner: JobRunner) -> None:
    with pytest.raises(NotFound, match="unknown job"):
        await runner.run_now("nope")


async def test_start_exposes_next_fire_times_and_stop_is_clean(runner: JobRunner) -> None:
    assert runner.next_fire_times() == {"mail_sync": None, "po_followups": None}
    runner.start()
    try:
        times = runner.next_fire_times()
        assert all(isinstance(t, datetime) for t in times.values())
        assert times["mail_sync"] is not None and times["mail_sync"].minute in (0, 30)
    finally:
        await runner.stop()
    assert runner.next_fire_times()["mail_sync"] is None
