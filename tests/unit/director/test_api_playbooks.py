"""The playbooks API and the follow-up job starting playbooks."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.autonomy import AutoAction
from director.policies import PoFacts
from director.testing import MemoryDirectorModule
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings

from .helpers import agent_reply

TODAY = date.today()


@pytest.fixture
def supplier() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def module(supplier: FakeAgentCaller) -> MemoryDirectorModule:
    return MemoryDirectorModule(supplier_comms=supplier)


@pytest.fixture
def client(module: MemoryDirectorModule) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="director",
        environment="test",
        events={"signing_secret": SecretStr("events")},
        ui={"jwt_secret": SecretStr("ui")},
        odoo={"url": "http://odoo.test:8069"},
    )
    app = create_application(settings, version=__version__, routers=[api_router], modules=[module])
    with TestClient(app) as c:
        yield c
    logger.remove()


async def _users(module: MemoryDirectorModule) -> None:
    for email, role in (("ana@x.com", "approver"), ("vic@x.com", "viewer")):
        await module.users.create(
            email=email,
            name=email.split("@")[0].title(),
            password_hash=hash_password("s3cret!!"),
            role=role,  # type: ignore[arg-type]
        )


def _token(client: TestClient, email: str) -> dict[str, str]:
    body = client.post("/api/auth/login", json={"email": email, "password": "s3cret!!"}).json()
    return {"Authorization": f"Bearer {body['token']}"}


def _late(name: str = "P00077") -> PoFacts:
    return PoFacts(
        po_id=77,
        po_name=name,
        partner_id=8,
        state="purchase",
        date_planned=date(2026, 9, 6),
        receipt_status="pending",
    )


async def test_playbooks_are_listed_started_by_hand_and_visible_on_approvals_and_the_board(
    client: TestClient, module: MemoryDirectorModule, supplier: FakeAgentCaller
) -> None:
    await _users(module)
    ana, viewer = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    module.exceptions.facts.append(_late())
    listed = client.get("/api/playbooks", headers=viewer).json()
    assert [p["name"] for p in listed] == [
        "internal_request",
        "late_order",
        "new_supplier_onboarding",
        "quote_round",
        "receipt_variance",
        "silent_rfq",
    ]
    late = next(p for p in listed if p["name"] == "late_order")
    assert late["active_runs"] == 0 and [s["id"] for s in late["steps"]][:2] == [
        "ask_eta",
        "wait_reply",
    ]

    # starting by hand: the ETA request goes to the supplier agent and needs a person
    supplier.replies.append(
        agent_reply(
            "request_eta",
            "pb_1_ask_eta",
            "awaiting_approval",
            "draft",
            approval_id=5,
            po_name="P00077",
        )
    )
    assert (
        client.post(
            "/api/playbooks/runs",
            json={"playbook": "late_order", "po_name": "P00077"},
            headers=viewer,
        ).status_code
        == 403
    )
    started = client.post(
        "/api/playbooks/runs",
        json={"playbook": "late_order", "po_name": "P00077", "partner_id": 8},
        headers=ana,
    )
    assert started.status_code == 201
    body = started.json()
    assert body["run"]["status"] == "waiting_approval" and body["position"]["step_id"] == "ask_eta"
    assert body["position"]["next_steps"][0] == "Wait two days for a reply"
    assert [s["step_id"] for s in body["steps"]] == ["ask_eta"]
    assert (
        client.post(
            "/api/playbooks/runs", json={"playbook": "nope", "po_name": "P00077"}, headers=ana
        ).status_code
        == 404
    )

    # the approval the agent created shows where it sits in the plan
    module.approvals.seed(
        5, kind="send_email", summary="Ask the date", po=(77, "P00077"), thread_id="pb_1_ask_eta"
    )
    case_id = body["run"]["case_id"]
    approval = client.get("/api/approvals/5", headers=viewer).json()
    assert approval["playbook"]["playbook"] == "late_order"
    assert approval["playbook"]["if_rejected"] is not None
    run = client.get("/api/playbooks/runs/1", headers=viewer).json()
    assert run["run"]["case_id"] == case_id
    # an action that ran alone inside the plan is keyed by the step thread id; the case shows it
    module.auto_actions.add(
        AutoAction(
            id=1,
            created_at=datetime.now(UTC),
            case_id="pb_1_ask_eta",
            agent="supplier_comms",
            kind="send_email",
            level="auto_notice",
            rule_id="hidraulica-date-requests",
            summary="Send delivery date request to Proveedor Hidraulica for P00077",
            po_id=77,
            po_name="P00077",
            partner_id=8,
        )
    )
    detail = client.get(f"/api/cases/{case_id}", headers=viewer).json()
    assert [a["rule_id"] for a in detail["auto_actions"]] == ["hidraulica-date-requests"]
    assert detail["case"]["status"] == "awaiting_approval"
    runs = client.get("/api/playbooks/runs?active=true", headers=viewer).json()
    assert [r["run"]["id"] for r in runs] == [1]
    counts = client.get("/api/playbooks", headers=viewer).json()
    assert next(p for p in counts if p["name"] == "late_order")["active_runs"] == 1

    cancelled = client.post("/api/playbooks/runs/1/cancel", headers=ana).json()
    assert cancelled["run"]["status"] == "cancelled" and "ana@x.com" in cancelled["run"]["summary"]
    assert client.post("/api/playbooks/runs/1/cancel", headers=ana).status_code == 409
    ticked = client.post("/api/playbooks/tick", headers=ana).json()
    assert ticked["active"] == 0 and ticked["moved"] == []
