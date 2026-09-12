"""Director ``/events`` and ``/jobs/{job}`` stubs with an in-memory inbox."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from injector import Binder, Module, singleton
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.inbox import EventInbox, MemoryEventInbox
from director.routers import events
from sc_core.a2a.events import SIGNATURE_HEADER, HmacSigner, encode_event
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.schema.events import InboundMailLinked, ScheduledTick
from sc_core.shared.time import utc_now

SECRET = "director-secret"


class _Inbox(Module):
    def __init__(self, inbox: MemoryEventInbox) -> None:
        self.inbox = inbox

    def configure(self, binder: Binder) -> None:
        binder.bind(EventInbox, to=self.inbox, scope=singleton)  # type: ignore[type-abstract]


@pytest.fixture
def inbox() -> MemoryEventInbox:
    return MemoryEventInbox()


@pytest.fixture
def client(inbox: MemoryEventInbox) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="director",
        environment="test",
        events={"signing_secret": SecretStr(SECRET)},
    )
    app = create_application(
        settings, version=__version__, routers=[events.router], modules=[_Inbox(inbox)]
    )
    with TestClient(app) as c:
        yield c
    logger.remove()


def _linked() -> InboundMailLinked:
    return InboundMailLinked(
        source="mail_sync",
        case_id="case_1",
        po_id=7,
        po_name="P00015",
        graph_message_id="AAMk1",
        confidence="exact",
        rule="header",
    )


def _post(
    client: TestClient, path: str, body: bytes, secret: str | None = SECRET
) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if secret:
        headers[SIGNATURE_HEADER] = HmacSigner(secret).sign(body)
    return client.post(path, content=body, headers=headers)


def test_valid_signature_stores_once(client: TestClient, inbox: MemoryEventInbox) -> None:
    event = _linked()
    first = _post(client, "/events", encode_event(event))
    assert first.status_code == 202 and first.json()["duplicate"] is False
    again = _post(client, "/events", encode_event(event))
    assert again.status_code == 202 and again.json()["duplicate"] is True
    assert list(inbox.events) == [event.event_id]


def test_bad_or_missing_signature_is_401(client: TestClient, inbox: MemoryEventInbox) -> None:
    body = encode_event(_linked())
    assert _post(client, "/events", body, secret="wrong").status_code == 401
    assert _post(client, "/events", body, secret=None).status_code == 401
    assert inbox.events == {}


def test_signed_but_malformed_event_is_422(client: TestClient) -> None:
    response = _post(client, "/events", b'{"type": "inbound_mail.linked"}')
    assert response.status_code == 422


def test_job_stub_records_tick_and_rejects_unknown_job(
    client: TestClient, inbox: MemoryEventInbox
) -> None:
    tick = ScheduledTick(
        source="scheduler",
        case_id="run_1",
        job_id="po_followups",
        run_id="run_1",
        scheduled_at=utc_now(),
    )
    ok = _post(client, "/jobs/po-followups", encode_event(tick))
    assert ok.status_code == 202 and inbox.of_type("scheduler.tick")
    assert _post(client, "/jobs/nope", encode_event(tick)).status_code == 404


def test_unconfigured_secret_rejects_everything(inbox: MemoryEventInbox) -> None:
    settings = Settings(_env_file=None, service_name="director", environment="test")
    app = create_application(settings, routers=[events.router], modules=[_Inbox(inbox)])
    with TestClient(app) as c:
        assert _post(c, "/events", encode_event(_linked())).status_code == 401
    logger.remove()
