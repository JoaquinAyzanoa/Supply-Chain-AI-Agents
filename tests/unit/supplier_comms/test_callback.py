"""The approval callback: signed, resumes the right case, safe to repeat, refuses mismatches."""

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
from sc_core.llm.testing import ScriptedChatClient, tool_call_result
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.events import BaseEvent
from supplier_comms.agent import SupplierCommsAgent
from supplier_comms.models import DraftOutput
from supplier_comms.routers import approvals
from supplier_comms.service import AgentProvider
from supplier_comms.testing import FakePorts

SECRET = "callback-secret"


class _Provider:
    def __init__(self, agent: SupplierCommsAgent) -> None:
        self.agent = agent

    async def get(self) -> SupplierCommsAgent:
        return self.agent


class _Publisher:
    def __init__(self) -> None:
        self.events: list[BaseEvent] = []

    async def publish(self, event: BaseEvent) -> bool:
        self.events.append(event)
        return True


class _Module(Module):
    def __init__(self, agent: SupplierCommsAgent, publisher: _Publisher) -> None:
        self.agent = agent
        self.publisher = publisher

    def configure(self, binder: Binder) -> None:
        binder.bind(AgentProvider, to=_Provider(self.agent), scope=singleton)  # type: ignore[arg-type]
        binder.bind(EventPublisher, to=self.publisher, scope=singleton)  # type: ignore[arg-type]


@pytest.fixture
def agent(make_agent: Any, chat: ScriptedChatClient) -> SupplierCommsAgent:
    chat.responses.extend(
        [
            tool_call_result("get_po_lines", {"po_name": "P00015"}),
            "listo",
            DraftOutput(
                subject="Solicitud de cotización", html_body="<p>Cotización, por favor.</p>"
            ),
        ]
    )
    return make_agent()


@pytest.fixture
def publisher() -> _Publisher:
    return _Publisher()


@pytest.fixture
def client(agent: SupplierCommsAgent, publisher: _Publisher) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="supplier_comms",
        environment="test",
        events={"signing_secret": SecretStr(SECRET)},
    )
    app = create_application(
        settings, routers=[approvals.router], modules=[_Module(agent, publisher)]
    )
    with TestClient(app) as c:
        yield c
    logger.remove()


def _odoo_callback(approval_id: int, thread_id: str, status: str = "approved") -> bytes:
    """Exactly what sc.approval._sc_callback_payload produces (sorted keys, compact)."""
    payload = {
        "approval_id": approval_id,
        "kind": "send_email",
        "status": status,
        "thread_id": thread_id,
        "case_id": thread_id,
        "run_id": "run_x",
        "resolved_by": "ana",
        "resolved_at": "2026-09-12 15:00:00",
        "reason": "",
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


async def test_callback_resumes_once_and_is_idempotent(
    client: TestClient, agent: SupplierCommsAgent, ports: FakePorts, publisher: _Publisher
) -> None:
    paused = await agent.run(
        SupplierCommsTask(kind="send_rfq", case_id="case_cb", po_name="P00015")
    )
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101

    first = _post(client, _odoo_callback(101, "case_cb"))
    assert first.status_code == 200, first.text
    assert first.json()["resumed"] is True and first.json()["result"]["outcome"]["status"] == "sent"
    assert ports.sent_ids == ["draft1"]

    again = _post(client, _odoo_callback(101, "case_cb"))
    assert again.status_code == 200 and again.json()["resumed"] is False
    assert again.json()["result"]["outcome"]["status"] == "sent"
    assert ports.sent_ids == ["draft1"]  # not sent twice
    assert ports.runs[paused.run_id]["status"] == "sent"

    # the director learns how the run ended, once, with a deterministic id
    [finished] = publisher.events
    assert finished.type == "agent.run_finished"
    assert finished.thread_id == "case_cb" and finished.status == "sent"  # type: ignore[attr-defined]
    assert finished.approval_id == 101 and finished.po_name == "P00015"  # type: ignore[attr-defined]
    assert finished.event_id.startswith("evt_") and finished.run_id == paused.run_id  # type: ignore[attr-defined]


async def test_callback_for_the_wrong_approval_is_refused(
    client: TestClient, agent: SupplierCommsAgent, ports: FakePorts
) -> None:
    await agent.run(SupplierCommsTask(kind="send_rfq", case_id="case_cb2", po_name="P00015"))
    wrong = _post(client, _odoo_callback(999, "case_cb2"))
    assert wrong.status_code == 409 and ports.sent_ids == []
    unknown = _post(client, _odoo_callback(101, "case_nope"))
    assert unknown.status_code == 404


def test_callback_needs_a_valid_signature(client: TestClient) -> None:
    body = _odoo_callback(101, "case_cb3")
    assert _post(client, body, secret="wrong").status_code == 401
    assert client.post("/approvals/callback", content=body).status_code == 401
    malformed = b'{"approval_id": "x"}'
    assert _post(client, malformed).status_code == 422
