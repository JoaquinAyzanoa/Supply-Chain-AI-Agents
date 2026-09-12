"""Odoo business events through ``POST /events``: signed, idempotent, routed to the workflow."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.routers import events
from director.store import MemoryCaseStore
from director.testing import MemoryDirectorModule
from sc_core.a2a.events import SIGNATURE_HEADER, HmacSigner, encode_event
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.schema import events as ev
from sc_core.schema.events import event_id_for

from .helpers import agent_reply, po_confirmed

SECRET = "director-secret"


@pytest.fixture
def cases() -> MemoryCaseStore:
    return MemoryCaseStore()


@pytest.fixture
def agent() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def client(cases: MemoryCaseStore, agent: FakeAgentCaller) -> Iterator[TestClient]:
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
        modules=[MemoryDirectorModule(supplier_comms=agent, cases=cases)],
    )
    with TestClient(app) as c:
        yield c
    logger.remove()


def _post(client: TestClient, body: bytes, secret: str | None = SECRET) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if secret:
        headers[SIGNATURE_HEADER] = HmacSigner(secret).sign(body)
    return client.post("/events", content=body, headers=headers)


def _odoo_body(event: ev.BaseEvent) -> bytes:
    """What the addon's ``sc.event.emitter`` sends: the envelope keys plus the payload."""
    return json.dumps(json.loads(encode_event(event)), ensure_ascii=False).encode("utf-8")


def test_po_confirmed_opens_a_case_and_sends_the_order(
    client: TestClient, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    agent.replies.append(
        agent_reply(
            "send_po",
            "odoo_po_66_purchase",
            "awaiting_approval",
            "cover drafted",
            approval_id=7,
            po_name="P00066",
        )
    )
    event = po_confirmed(event_id=event_id_for("odoo.purchase_confirmed", 66, "purchase"))
    response = _post(client, _odoo_body(event))
    assert response.status_code == 202 and response.json()["dispatched"] is True

    task = json.loads(agent.sent[0].task_json)
    assert task["kind"] == "send_po" and task["po_name"] == "P00066"
    [case] = cases.cases.values()
    assert case.kind == "eta" and case.po_name == "P00066" and case.status == "awaiting_approval"
    promise = [e for e in cases.case_events if e.kind == "promise"]
    assert len(promise) == 1 and promise[0].payload["amount_total"] == 0.0
    assert promise[0].payload["date_planned"].startswith("2026-10-01")


def test_duplicate_delivery_is_one_case(
    client: TestClient, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    agent.replies.append(
        agent_reply("send_po", "odoo_po_66_purchase", "sent", "sent", po_name="P00066")
    )
    event = po_confirmed(event_id=event_id_for("odoo.purchase_confirmed", 66, "purchase"))
    assert _post(client, _odoo_body(event)).status_code == 202
    again = _post(client, _odoo_body(event))
    assert again.status_code == 202 and again.json()["duplicate"] is True
    assert len(agent.sent) == 1 and len(cases.cases) == 1


def test_invalid_secret_is_rejected(client: TestClient, cases: MemoryCaseStore) -> None:
    assert _post(client, _odoo_body(po_confirmed()), secret="wrong").status_code == 401
    assert _post(client, _odoo_body(po_confirmed()), secret=None).status_code == 401
    assert cases.cases == {}


def test_unknown_event_type_is_422(client: TestClient) -> None:
    body = _odoo_body(po_confirmed()).replace(b"odoo.purchase_confirmed", b"odoo.nope")
    assert _post(client, body).status_code == 422


def test_receipt_and_approval_events_are_recorded(
    client: TestClient, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    receipt = ev.OdooReceiptValidated(
        source="odoo",
        case_id="odoo_picking_5_done",
        picking_id=5,
        picking_name="WH/IN/00005",
        po_id=66,
        po_name="P00066",
        partner_id=9,
    )
    assert _post(client, _odoo_body(receipt)).status_code == 202
    resolved = ev.OdooApprovalResolved(
        source="odoo",
        case_id="odoo_approval_7",
        approval_id=7,
        kind="send_email",
        status="approved",
        thread_id="odoo_po_66_purchase",
        po_id=66,
        po_name="P00066",
        resolved_by="admin",
    )
    assert _post(client, _odoo_body(resolved)).status_code == 202
    assert agent.sent == []
    kinds = {c.kind for c in cases.cases.values()}
    assert kinds == {"receipt", "rfq"}
    notes = [e.payload["text"] for e in cases.case_events if e.kind == "note"]
    assert any("WH/IN/00005" in n for n in notes) and any("approval 7" in n for n in notes)
