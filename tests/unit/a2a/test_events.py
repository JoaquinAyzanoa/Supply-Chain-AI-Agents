"""HMAC signing, publisher retries and the outbox."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from sc_core.a2a.events import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    EventPublisher,
    HmacSigner,
    MemoryOutbox,
    encode_event,
)
from sc_core.infra.settings import EventsCfg
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import ConfigurationError
from sc_core.shared.time import utc_now


def _cfg(**overrides: object) -> EventsCfg:
    values: dict[str, object] = {
        "signing_secret": SecretStr("s3cret"),
        "director_url": "http://director.test",
        "max_attempts": 3,
        "backoff_seconds": 0.1,
    }
    values.update(overrides)
    return EventsCfg(**values)  # type: ignore[arg-type]


def _tick(run_id: str = "run_1") -> ScheduledTick:
    return ScheduledTick(
        source="scheduler",
        case_id=run_id,
        job_id="mail_sync",
        run_id=run_id,
        scheduled_at=utc_now(),
    )


class Director:
    """Scripted director: a list of status codes (or exceptions) per request."""

    def __init__(self, script: list[int | Exception]) -> None:
        self.script = script
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return httpx.Response(item, json={"accepted": item < 300})

        return httpx.MockTransport(handler)


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _publisher(
    director: Director, cfg: EventsCfg | None = None
) -> tuple[EventPublisher, MemoryOutbox, Sleeps]:
    outbox, sleeps = MemoryOutbox(), Sleeps()
    pub = EventPublisher(
        cfg or _cfg(),
        outbox=outbox,
        http=httpx.AsyncClient(transport=director.transport()),
        sleep=sleeps,
    )
    return pub, outbox, sleeps


def test_signer_roundtrip_and_requires_secret() -> None:
    signer = HmacSigner("s3cret")
    body = b'{"a": 1}'
    sig = signer.sign(body)
    assert sig.startswith("sha256=") and signer.verify(body, sig)
    assert not signer.verify(body + b" ", sig) and not signer.verify(body, None)
    assert not HmacSigner("other").verify(body, sig)
    with pytest.raises(ConfigurationError):
        HmacSigner("")


async def test_publish_signs_body_and_sets_headers() -> None:
    director = Director([202])
    pub, outbox, _ = _publisher(director)
    event = _tick()
    assert await pub.publish(event) is True
    request = director.requests[0]
    assert str(request.url) == "http://director.test/events"
    assert request.content == encode_event(event)
    assert HmacSigner("s3cret").verify(request.content, request.headers[SIGNATURE_HEADER])
    assert request.headers[EVENT_ID_HEADER] == event.event_id
    assert outbox.rows == {}


async def test_retries_transient_then_succeeds() -> None:
    director = Director([httpx.ConnectError("down"), 503, 202])
    pub, outbox, sleeps = _publisher(director)
    assert await pub.publish(_tick()) is True
    assert len(director.requests) == 3 and sleeps.delays == [0.1, 0.2]
    assert outbox.rows == {}


async def test_non_retryable_goes_to_outbox_without_retry() -> None:
    director = Director([401])
    pub, outbox, sleeps = _publisher(director)
    event = _tick()
    assert await pub.publish(event) is False
    assert len(director.requests) == 1 and sleeps.delays == []
    row = outbox.rows[event.event_id]
    assert row["attempts"] == 1 and "401" in row["last_error"]


async def test_exhausted_retries_then_outbox_flush_delivers_in_order() -> None:
    director = Director([503, 503, 503, 503, 503, 503])
    pub, outbox, _ = _publisher(director)
    first, second = _tick("run_1"), _tick("run_2")
    assert await pub.publish(first) is False and await pub.publish(second) is False
    assert list(outbox.rows) == [first.event_id, second.event_id]

    director.script[:] = [202, 202]
    assert await pub.flush_outbox() == 2
    assert outbox.rows == {}
    delivered = [r.headers[EVENT_ID_HEADER] for r in director.requests[-2:]]
    assert delivered == [first.event_id, second.event_id]


async def test_flush_stops_at_first_failure_to_keep_order() -> None:
    director = Director([500, 500, 500, 500, 500, 500])
    pub, outbox, _ = _publisher(director)
    a, b = _tick("a"), _tick("b")
    await pub.publish(a)
    await pub.publish(b)
    director.script[:] = [500]
    assert await pub.flush_outbox() == 0
    assert list(outbox.rows) == [a.event_id, b.event_id]
    assert outbox.rows[a.event_id]["attempts"] == 2
