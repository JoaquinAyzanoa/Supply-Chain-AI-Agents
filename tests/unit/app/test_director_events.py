"""Director ``/events`` and ``/jobs/{job}`` with in-memory inbox and a fake agent."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.inbox import MemoryEventInbox, MemoryEventResults
from director.routers import events
from director.testing import MemoryDirectorModule
from sc_core.a2a.events import SIGNATURE_HEADER, HmacSigner, encode_event
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.schema.events import InboundMailLinked, InboundMailUnlinked, ScheduledTick
from sc_core.shared.errors import ExternalServiceError
from sc_core.shared.time import utc_now
from tests.unit.director.helpers import agent_reply

SECRET = "director-secret"


@pytest.fixture
def inbox() -> MemoryEventInbox:
    return MemoryEventInbox()


@pytest.fixture
def results() -> MemoryEventResults:
    return MemoryEventResults()


@pytest.fixture
def agent() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def client(
    inbox: MemoryEventInbox, results: MemoryEventResults, agent: FakeAgentCaller
) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="director",
        environment="test",
        events={"signing_secret": SecretStr(SECRET)},
    )
    app = create_application(
        settings,
        version=__version__,
        routers=[events.router],
        modules=[MemoryDirectorModule(inbox, results, agent)],
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


def test_valid_signature_stores_once_and_dispatches_once(
    client: TestClient, inbox: MemoryEventInbox, agent: FakeAgentCaller, results: MemoryEventResults
) -> None:
    agent.replies.append(
        agent_reply("handle_inbound", "case_1", "awaiting_approval", "proposal", approval_id=1)
    )
    event = _linked()
    first = _post(client, "/events", encode_event(event))
    assert first.status_code == 202 and first.json()["duplicate"] is False
    assert first.json()["dispatched"] is True
    again = _post(client, "/events", encode_event(event))
    assert again.status_code == 202 and again.json()["duplicate"] is True
    assert again.json()["dispatched"] is False
    assert list(inbox.events) == [event.event_id]

    # the background task ran inside the TestClient request: one task, the right kind
    assert len(agent.sent) == 1
    task = json.loads(agent.sent[0].task_json)
    assert task["kind"] == "handle_inbound" and task["po_name"] == "P00015"
    assert task["graph_message_id"] == "AAMk1" and task["case_id"] == "case_1"
    assert agent.sent[0].case_id.startswith("case_")  # the orchestrator's case: Langfuse session
    recorded = results.results[event.event_id]
    [update] = recorded["updates"]
    assert update["status"] == "awaiting_approval" and update["detail"]["task"] == "handle_inbound"


def test_unlinked_event_becomes_resolve_task(
    client: TestClient, agent: FakeAgentCaller, results: MemoryEventResults
) -> None:
    agent.replies.append(
        agent_reply("resolve_unlinked", "case_2", "escalated", "could not pick", po_name=None)
    )
    event = InboundMailUnlinked(
        source="mail_sync",
        case_id="case_2",
        graph_message_id="AAMk2",
        sender_address="v@x.com",
        partner_id=42,
        open_po_names=["P00015", "P00016"],
    )
    assert _post(client, "/events", encode_event(event)).status_code == 202
    task = json.loads(agent.sent[0].task_json)
    assert task["kind"] == "resolve_unlinked" and task["candidate_po_names"] == ["P00015", "P00016"]
    [update] = results.results[event.event_id]["updates"]
    assert update["status"] == "escalated"


def test_agent_failure_is_recorded_not_raised(
    client: TestClient, agent: FakeAgentCaller, results: MemoryEventResults
) -> None:
    agent.replies.append(ExternalServiceError("agent down", service="a2a"))
    event = _linked()
    assert _post(client, "/events", encode_event(event)).status_code == 202
    [update] = results.results[event.event_id]["updates"]
    assert update["status"] == "escalated" and "agent down" in update["detail"]["summary"]


def test_bad_or_missing_signature_is_401(client: TestClient, inbox: MemoryEventInbox) -> None:
    body = encode_event(_linked())
    assert _post(client, "/events", body, secret="wrong").status_code == 401
    assert _post(client, "/events", body, secret=None).status_code == 401
    assert inbox.events == {}


def test_signed_but_malformed_event_is_422(client: TestClient) -> None:
    response = _post(client, "/events", b'{"type": "inbound_mail.linked"}')
    assert response.status_code == 422


def test_job_stub_records_tick_and_rejects_unknown_job(
    client: TestClient, inbox: MemoryEventInbox, agent: FakeAgentCaller
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
    # the job ran inside the request (memory module: recorded no-op) and reported back
    [update] = ok.json()["result"]["updates"]
    assert update["kind"] == "job" and update["detail"]["status"] == "not_implemented"
    again = _post(client, "/jobs/po-followups", encode_event(tick))
    assert again.json()["duplicate"] is True and again.json()["result"] is None
    assert _post(client, "/jobs/nope", encode_event(tick)).status_code == 404
    assert agent.sent == []  # ticks are not dispatched to the supplier agent


def test_unconfigured_secret_rejects_everything(inbox: MemoryEventInbox) -> None:
    settings = Settings(_env_file=None, service_name="director", environment="test")
    app = create_application(
        settings, routers=[events.router], modules=[MemoryDirectorModule(inbox)]
    )
    with TestClient(app) as c:
        assert _post(c, "/events", encode_event(_linked())).status_code == 401
    logger.remove()
