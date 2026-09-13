"""The director's side of sourcing: the API, the hourly job and the award edits."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.handlers.jobs import SourcingJob
from director.testing import MemoryDirectorModule
from sc_core.a2a import AgentReply
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.schema.a2a import Outcome, SourcingResult

from .helpers import tick


def sourcing_reply(
    kind: str,
    case_id: str,
    status: str,
    *,
    approval_id: int | None = None,
    round_id: int | None = None,
) -> AgentReply:
    result = SourcingResult(
        kind=kind,  # type: ignore[arg-type]
        case_id=case_id,
        run_id="run_s1",
        outcome=Outcome(status=status, summary=f"{kind} {status}", approval_id=approval_id),  # type: ignore[arg-type]
        round_id=round_id,
    )
    return AgentReply(
        status="input_required" if status == "awaiting_approval" else "completed",
        text=result.model_dump_json(),
    )


@pytest.fixture
def sourcing() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def module(sourcing: FakeAgentCaller) -> MemoryDirectorModule:
    return MemoryDirectorModule(sourcing=sourcing)


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


ROUND = {
    "id": 1,
    "case_id": "round_abc",
    "status": "open",
    "source_po_name": "P00081",
    "incumbent_partner_id": 8,
    "basket": [
        {
            "product_id": 1,
            "product": "[CBEA-LHN] Válvula",
            "qty": 10,
            "last_paid": 104.16,
            "currency": "USD",
        }
    ],
    "deadline": "2026-09-19T09:00:00Z",
    "rfqs": [
        {
            "partner_id": 8,
            "partner_name": "Proveedor Hidraulica",
            "po_id": 81,
            "po_name": "P00081",
            "status": "sent",
        },
        {
            "partner_id": 9,
            "partner_name": "Hidráulica Alterna SAC",
            "po_id": 901,
            "po_name": "P00901",
            "status": "sent",
        },
    ],
    "created_at": "2026-09-14T09:00:00Z",
    "updated_at": "2026-09-14T09:00:00Z",
}


async def test_rounds_are_listed_started_and_compared_from_the_control_tower(
    client: TestClient, module: MemoryDirectorModule, sourcing: FakeAgentCaller
) -> None:
    await _users(module)
    ana, viewer = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    module.sourcing_source.rows.append(ROUND)
    listed = client.get("/api/sourcing/rounds?partner_id=9", headers=viewer).json()
    assert [r["id"] for r in listed] == [1]
    assert client.get("/api/sourcing/rounds?partner_id=10", headers=viewer).json() == []
    assert client.get("/api/sourcing/rounds/1", headers=viewer).json()["source_po_name"] == "P00081"
    assert client.get("/api/sourcing/rounds/2", headers=viewer).status_code == 404

    # starting a round needs an approver; the task reaches the sourcing agent on its own case
    assert (
        client.post("/api/sourcing/rounds", json={"po_name": "P00081"}, headers=viewer).status_code
        == 403
    )
    assert client.post("/api/sourcing/rounds", json={}, headers=ana).status_code == 422
    sourcing.replies.append(sourcing_reply("quote_round", "round_x", "sent", round_id=1))
    started = client.post(
        "/api/sourcing/rounds", json={"po_name": "P00081", "partner_ids": [9]}, headers=ana
    )
    assert started.status_code == 202
    body = started.json()
    assert body["status"] == "sent" and body["case_code"]
    task = json.loads(sourcing.sent[0].task_json)
    assert (
        task["kind"] == "quote_round" and task["po_name"] == "P00081" and task["partner_ids"] == [9]
    )
    assert task["case_id"].startswith("round_") and "ana@x.com" in task["reason"]
    case = list(module.cases.cases.values())[0]
    assert case.kind == "sourcing" and case.po_name == "P00081" and case.status == "done"

    # comparing now: a fresh thread that names the round; a closed round is refused
    sourcing.replies.append(
        sourcing_reply("compare_quotes", "round_abc_cmp", "awaiting_approval", approval_id=41)
    )
    compared = client.post("/api/sourcing/rounds/1/compare", headers=ana)
    assert compared.status_code == 202 and compared.json()["approval_id"] == 41
    task = json.loads(sourcing.sent[1].task_json)
    assert task["kind"] == "compare_quotes" and task["round_id"] == 1
    assert task["case_id"].startswith("round_abc_cmp_") and task["case_id"] != "round_abc"
    module.sourcing_source.rows[0] = {**ROUND, "status": "awarded"}
    assert client.post("/api/sourcing/rounds/1/compare", headers=ana).status_code == 409

    # a counter-offer by hand
    sourcing.replies.append(
        sourcing_reply("counter_offer", "offer_x", "awaiting_approval", approval_id=42)
    )
    offered = client.post(
        "/api/sourcing/negotiate", json={"po_name": "P00081", "target_price": 100}, headers=ana
    )
    assert offered.status_code == 202 and offered.json()["approval_id"] == 42
    task = json.loads(sourcing.sent[2].task_json)
    assert task["kind"] == "counter_offer" and task["target_price"] == 100.0


async def test_every_invited_rfq_gets_its_own_case_on_the_board(
    module: MemoryDirectorModule, sourcing: FakeAgentCaller
) -> None:
    from sc_core.schema.a2a import InvitedRfq, Need, SourcingTask

    result = SourcingResult(
        kind="quote_round",
        case_id="round_plan_run_1",
        run_id="run_s2",
        outcome=Outcome(status="sent", summary="quote round #4 started: 2 supplier(s) invited"),
        round_id=4,
        invited=[
            InvitedRfq(
                partner_id=8, partner_name="Proveedor Hidraulica", po_name="P00090", status="sent"
            ),
            InvitedRfq(
                partner_id=10,
                partner_name="Importadora del Sur SAC",
                po_name="P00091",
                status="no_email",
            ),
        ],
    )
    sourcing.replies.append(AgentReply(status="completed", text=result.model_dump_json()))
    task = SourcingTask(
        kind="quote_round", case_id="round_plan_run_1", needs=[Need(product_id=1, qty=30)]
    )
    await module.sourcing.run(task)
    by_po = {c.po_name: c for c in module.cases.cases.values()}
    assert by_po[None].kind == "sourcing" and by_po[None].status == "done"
    assert by_po["P00090"].kind == "rfq" and by_po["P00090"].status == "done"
    assert by_po["P00091"].status == "escalated" and "no email" in (by_po["P00091"].summary or "")
    sent = [
        e
        for e in module.cases.case_events
        if e.kind == "task_sent" and e.payload.get("po_name") == "P00090"
    ]
    assert sent and sent[0].payload["thread_id"] == "round_plan_run_1_rfq8"
    assert await module.cases.find_by_thread("round_plan_run_1_rfq8") is not None


async def test_the_hourly_job_compares_the_due_rounds(
    module: MemoryDirectorModule, sourcing: FakeAgentCaller
) -> None:
    module.sourcing_source.due.append(ROUND)
    sourcing.replies.append(
        sourcing_reply("compare_quotes", "round_abc_cmp_run_7", "awaiting_approval", approval_id=43)
    )
    job = SourcingJob(module.sourcing_source, module.sourcing)
    summary = await job.run("sourcing_rounds", tick("sourcing_rounds", "run_7"))
    assert summary["status"] == "ok" and len(summary["compared"]) == 1
    assert summary["compared"][0]["round_id"] == 1 and summary["compared"][0]["approval_id"] == 43
    task = json.loads(sourcing.sent[0].task_json)
    assert task["case_id"] == "round_abc_cmp_run_7" and task["round_id"] == 1
    assert "deadline reached" in task["reason"]
    [case] = module.cases.cases.values()
    assert case.kind == "sourcing" and case.status == "awaiting_approval"
    assert (await job.run("other", tick("other", "run_8")))["status"] == "not_implemented"


async def test_award_and_offer_edits_are_validated(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    ana = _token(client, "ana@x.com")
    module.approvals.seed(
        5,
        kind="award",
        summary="Award",
        payload={"comparison": {"quotes": []}},
        po=None,
        thread_id="round_abc_cmp_1",
    )
    module.approvals.seed(
        6,
        kind="negotiation_offer",
        summary="Offer",
        payload={"offered_price": 100},
        po=(81, "P00081"),
        thread_id="offer_x",
    )
    module.approvals.seed(
        7,
        kind="partner_create",
        summary="New",
        payload={"suggested_name": "Aceros Lima"},
        po=None,
        thread_id="case_new1",
    )
    assert (
        client.post(
            "/api/approvals/5/resolve",
            json={"status": "approved", "edited_payload": {"partner_id": "nope"}},
            headers=ana,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/approvals/5/resolve",
            json={"status": "approved", "edited_payload": {"partner_id": 9}},
            headers=ana,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/approvals/6/resolve",
            json={"status": "approved", "edited_payload": {"offered_price": -1}},
            headers=ana,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/approvals/6/resolve",
            json={"status": "approved", "edited_payload": {"offered_price": 101.5}},
            headers=ana,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/approvals/7/resolve",
            json={"status": "approved", "edited_payload": {"name": "Aceros Lima SAC"}},
            headers=ana,
        ).status_code
        == 200
    )
    resolved = {a.id: a for a in module.approvals.rows.values()}
    assert (
        resolved[5].status == "approved"
        and resolved[6].status == "approved"
        and resolved[7].status == "approved"
    )
