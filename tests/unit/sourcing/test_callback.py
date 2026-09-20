"""The approval callback: signed, answers Odoo at once, resumes in the background."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from injector import Binder, Module, singleton
from loguru import logger
from pydantic import SecretStr

from sc_core.a2a.events import SIGNATURE_HEADER, EventPublisher, HmacSigner
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import SourcingTask
from sc_core.schema.events import BaseEvent
from sourcing.api.routers import approvals
from sourcing.graph.nodes.negotiate import JustificationText
from sourcing.graph.runner import SourcingAgent
from sourcing.infra.service import AgentProvider
from sourcing.testing import FakeSourcingPorts, supplier_reply

from .test_graph import _rfq

SECRET = "callback-secret"


class _Provider:
    def __init__(self, agent: SourcingAgent) -> None:
        self.agent = agent

    async def get(self) -> SourcingAgent:
        return self.agent


class _Publisher:
    def __init__(self) -> None:
        self.events: list[BaseEvent] = []

    async def publish(self, event: BaseEvent) -> bool:
        self.events.append(event)
        return True


class _Module(Module):
    def __init__(self, agent: SourcingAgent, publisher: _Publisher) -> None:
        self.agent = agent
        self.publisher = publisher

    def configure(self, binder: Binder) -> None:
        binder.bind(AgentProvider, to=_Provider(self.agent), scope=singleton)  # type: ignore[arg-type]
        binder.bind(EventPublisher, to=self.publisher, scope=singleton)  # type: ignore[arg-type]


@pytest.fixture
def publisher() -> _Publisher:
    return _Publisher()


@pytest.fixture
def client(make_agent: Any, publisher: _Publisher) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="sourcing",
        environment="test",
        events={"signing_secret": SecretStr(SECRET)},
    )
    agent = make_agent()
    app = create_application(
        settings, routers=[approvals.router], modules=[_Module(agent, publisher)]
    )
    app.state.agent = agent
    with TestClient(app) as c:
        yield c
    logger.remove()


def _odoo_callback(approval_id: int, thread_id: str, details: dict[str, Any] | None) -> bytes:
    payload = {
        "approval_id": approval_id,
        "kind": "negotiation_offer",
        "status": "approved",
        "thread_id": thread_id,
        "case_id": thread_id,
        "run_id": "run_x",
        "resolved_by": "ana",
        "resolved_by_name": "Ana",
        "resolved_at": "2026-09-14 15:00:00",
        "reason": "",
        "details": details,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _post(client: TestClient, body: bytes, secret: str = SECRET) -> Any:
    return client.post(
        "/approvals/callback",
        content=body,
        headers={
            "Content-Type": "application/json",
            SIGNATURE_HEADER: HmacSigner(secret).sign(body),
        },
    )


async def test_callback_answers_at_once_and_finishes_the_run_in_the_background(
    client: TestClient, ports: FakeSourcingPorts, chat: ScriptedChatClient, publisher: _Publisher
) -> None:
    agent: SourcingAgent = client.app.state.agent  # type: ignore[attr-defined]
    ports.rfqs[81] = await _rfq(ports, 81)
    ports.supplier_replied(81, 110.0)
    chat.responses.append(JustificationText(text="Our last price; terms unchanged."))
    paused = await agent.run(
        SourcingTask(kind="counter_offer", case_id="offer_cb", po_name="P00081")
    )
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101

    ports.supplier_replies.append(supplier_reply("counter_offer", "offer_cb_offer1", "sent"))
    first = _post(client, _odoo_callback(101, "offer_cb", {"offered_price": 102.0}))
    assert first.status_code == 200, first.text
    # Odoo hears "resumed" right away; the run itself completes after the response
    assert first.json()["resumed"] is True
    assert first.json()["result"]["outcome"]["status"] == "awaiting_approval"
    assert (
        ports.sent_tasks[-1]["kind"] == "counter_offer"
        and ports.runs[paused.run_id]["status"] == "sent"
    )
    [finished] = publisher.events
    assert finished.type == "agent.run_finished" and finished.status == "sent"  # type: ignore[attr-defined]

    again = _post(client, _odoo_callback(101, "offer_cb", None))
    assert again.status_code == 200 and again.json()["resumed"] is False
    assert len(ports.sent_tasks) == 1  # not sent twice
    assert _post(client, _odoo_callback(101, "offer_cb", None), secret="wrong").status_code == 401
    assert _post(client, _odoo_callback(999, "nowhere", None)).status_code == 404
