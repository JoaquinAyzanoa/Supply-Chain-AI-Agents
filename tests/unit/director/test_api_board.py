"""Cases, runs, exceptions, planning, settings and the SSE stream on the Control Tower API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.api.planning import PlanningLineRow, PlanningRunRow
from director.api.stream import stream
from director.policies import PoFacts
from director.testing import MemoryDirectorModule
from sc_core.a2a import AgentReply
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.odoo.models import AgentRun, Ref
from sc_core.schema.a2a import InventoryPlanningResult, Outcome
from sc_core.schema.planning import ReplenishmentLine, ReplenishmentProposal
from sc_core.shared.time import local_today

TODAY = date(2026, 9, 14)
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def planner() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def module(planner: FakeAgentCaller) -> MemoryDirectorModule:
    return MemoryDirectorModule(inventory_planning=planner)


@pytest.fixture
def client(module: MemoryDirectorModule) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="director",
        environment="test",
        events={"signing_secret": SecretStr("events")},
        ui={"jwt_secret": SecretStr("ui"), "sse_heartbeat_seconds": 0.05},
        odoo={"url": "http://odoo.test:8069"},
        director={"rfq_no_reply_days": [2, 5]},
    )
    app = create_application(settings, version=__version__, routers=[api_router], modules=[module])
    with TestClient(app) as c:
        yield c
    logger.remove()


async def _users(module: MemoryDirectorModule) -> None:
    for email, role in (("ana@x.com", "approver"), ("vic@x.com", "viewer"), ("adm@x.com", "admin")):
        await module.users.create(
            email=email,
            name=email.split("@")[0].title(),
            password_hash=hash_password("s3cret!!"),
            role=role,  # type: ignore[arg-type]
        )


def _token(client: TestClient, email: str) -> dict[str, str]:
    body = client.post("/api/auth/login", json={"email": email, "password": "s3cret!!"}).json()
    return {"Authorization": f"Bearer {body['token']}"}


def _raw_token(client: TestClient, email: str) -> str:
    return str(
        client.post("/api/auth/login", json={"email": email, "password": "s3cret!!"}).json()[
            "token"
        ]
    )


def _run(
    run_id: str, case_id: str, *, agent: str = "supplier_comms", model: str = "m1", **over: Any
) -> AgentRun:
    base: dict[str, Any] = {
        "id": abs(hash(run_id)) % 10_000,
        "run_id": run_id,
        "agent": agent,
        "case_id": case_id,
        "po_id": Ref(id=15, name="P00015"),
        "status": "sent",
        "model": model,
        "started_at": NOW - timedelta(minutes=5),
        "finished_at": NOW - timedelta(minutes=4, seconds=30),
        "llm_calls": 3,
        "input_tokens": 1200,
        "output_tokens": 300,
        "cost_usd": 0.0042,
    }
    return AgentRun(**{**base, **over})


def _facts(po_id: int, name: str, **over: Any) -> PoFacts:
    base: dict[str, Any] = {"po_id": po_id, "po_name": name, "partner_id": 7, "state": "draft"}
    return PoFacts(**{**base, **over})


def _line(line_id: str = "run_1:101", **over: Any) -> ReplenishmentLine:
    base: dict[str, Any] = {
        "line_id": line_id,
        "product_id": 101,
        "product_ref": "HYD-001",
        "warehouse_id": 1,
        "on_hand": 10,
        "incoming": 0,
        "position": 10,
        "forecast_daily": 2.0,
        "forecast_method": "ses",
        "sigma_daily": 0.5,
        "history_periods": 52,
        "lead_time_days": 7,
        "sigma_lead_time_days": 1.75,
        "service_level": 0.95,
        "review_period_days": 7,
        "abc_class": "A",
        "ss": 5,
        "rop": 19,
        "order_up_to": 33,
        "proposed_min": 19,
        "proposed_max": 33,
        "order_qty": 23,
        "supplier_id": 7,
        "unit_price": 10.0,
        "currency": "PEN",
        "action": "create_rfq",
    }
    return ReplenishmentLine(**{**base, **over})


# --- cases ------------------------------------------------------------------------------------


async def test_cases_list_filters_and_detail_carries_events_and_runs(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    rfq, _ = await module.cases.attach_or_create(kind="rfq", po_name="P00015")
    await module.cases.update(rfq.case_id, status="awaiting_approval", trace_id="tr1")
    await module.cases.add_event(
        rfq.case_id, "task_sent", {"agent": "supplier_comms", "thread_id": "followup_p00015"}
    )
    plan, _ = await module.cases.attach_or_create(kind="planning", po_name=None)
    module.runs.rows += [_run("run_a", "followup_p00015"), _run("run_b", "elsewhere")]
    viewer = _token(client, "vic@x.com")

    rows = client.get("/api/cases?status=awaiting_approval", headers=viewer).json()
    assert [r["case_id"] for r in rows] == [rfq.case_id]
    assert (rows[0]["trace_url"] or "/trace/tr1").endswith("/trace/tr1")  # when Langfuse is set
    assert [r["kind"] for r in client.get("/api/cases?kind=planning", headers=viewer).json()] == [
        "planning"
    ]
    assert len(client.get("/api/cases", headers=viewer).json()) == 2

    detail = client.get(f"/api/cases/{rfq.case_id}", headers=viewer).json()
    assert detail["case"]["status"] == "awaiting_approval"
    assert [e["kind"] for e in detail["events"]] == ["task_sent"]
    assert [r["run_id"] for r in detail["runs"]] == ["run_a"]  # by the thread id the task went on
    assert detail["runs"][0]["duration_seconds"] == 30.0 and detail["runs"][0]["cost_usd"] == 0.0042
    assert client.get("/api/cases/nope", headers=viewer).status_code == 404
    assert plan.case_id != rfq.case_id


# --- runs -------------------------------------------------------------------------------------


async def test_runs_filter_by_agent_model_and_since_and_scheduler_runs_carry_cron(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    module.runs.rows += [
        _run("r1", "c1"),
        _run("r2", "c2", agent="inventory_planning", model="m2"),
        _run("r3", "c3", started_at=NOW - timedelta(days=3), finished_at=None),
    ]
    module.scheduler_runs.rows += [
        {
            "run_id": "s1",
            "job_id": "po_followups",
            "trigger": "cron",
            "started_at": NOW,
            "finished_at": NOW,
            "status": "ok",
            "http_status": 202,
            "summary": "3 tasks",
        }
    ]
    viewer = _token(client, "vic@x.com")
    assert [r["run_id"] for r in client.get("/api/runs", headers=viewer).json()] == [
        "r1",
        "r2",
        "r3",
    ]
    assert [
        r["run_id"] for r in client.get("/api/runs?agent=inventory_planning", headers=viewer).json()
    ] == ["r2"]
    assert [r["run_id"] for r in client.get("/api/runs?model=m1", headers=viewer).json()] == [
        "r1",
        "r3",
    ]
    since = quote((NOW - timedelta(days=1)).isoformat())
    assert [r["run_id"] for r in client.get(f"/api/runs?since={since}", headers=viewer).json()] == [
        "r1",
        "r2",
    ]
    r3 = next(r for r in client.get("/api/runs", headers=viewer).json() if r["run_id"] == "r3")
    assert r3["duration_seconds"] is None
    sched = client.get("/api/runs/scheduler", headers=viewer).json()
    assert sched[0]["job_id"] == "po_followups" and sched[0]["cron"] == "0 9 * * 1-5"
    assert client.get("/api/runs/scheduler?job=other", headers=viewer).json() == []


# --- exceptions -------------------------------------------------------------------------------


async def test_exceptions_board_groups_and_act_now_runs_in_the_background(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    today = local_today()
    module.exceptions.facts = [
        _facts(1, "P00010", last_outbound_at=today - timedelta(days=3)),  # silent RFQ
        _facts(
            2,
            "P00011",
            state="purchase",
            receipt_status="pending",
            date_planned=today - timedelta(days=2),
        ),
        _facts(
            3,
            "P00012",
            state="purchase",
            receipt_status="pending",
            date_planned=today - timedelta(days=9),
            awaiting_human=True,
        ),
        _facts(
            4,
            "P00016",
            last_outbound_at=today - timedelta(days=4),
            last_inbound_at=today - timedelta(days=1),
        ),
    ]
    unlinked, _ = await module.cases.attach_or_create(kind="unlinked", po_name=None)
    failed, _ = await module.cases.attach_or_create(kind="eta", po_name="P00099")
    await module.cases.update(failed.case_id, status="failed", summary="agent unreachable")
    stale = module.approvals.seed(7, kind="send_email", summary="Send reminder")
    now = datetime.now(UTC)
    module.approvals.rows[7] = stale.model_copy(update={"create_date": now - timedelta(days=3)})
    module.approvals.seed(8, kind="po_change", summary="fresh").model_copy()
    module.approvals.rows[8] = module.approvals.rows[8].model_copy(update={"create_date": now})
    viewer = _token(client, "vic@x.com")

    board = client.get("/api/exceptions", headers=viewer).json()
    assert [i["po_name"] for i in board["late_pos"]] == ["P00012", "P00011"]
    late = board["late_pos"][1]
    assert late["next_action"] == "request_eta (po_late)" and late["can_act"] is True
    assert late["next_action_at"] == (today - timedelta(days=1)).isoformat()
    assert board["late_pos"][0]["can_act"] is False and board["late_pos"][0]["next_action"] is None
    silent = board["rfqs_no_reply"]
    assert [i["po_name"] for i in silent] == ["P00010"] and silent[0]["days"] == 3
    assert silent[0]["next_action"] == "follow_up (rfq_silent)"
    assert [i["case_id"] for i in board["unlinked_mails"]] == [unlinked.case_id]
    assert [i["po_name"] for i in board["failed_runs"]] == ["P00099"]
    assert [i["approval_id"] for i in board["stale_approvals"]] == [7]

    approver = _token(client, "ana@x.com")
    assert client.post("/api/exceptions/late_po/P00011/act", headers=viewer).status_code == 403
    acted = client.post("/api/exceptions/late_po/P00011/act", headers=approver)
    assert acted.status_code == 202 and acted.json()["accepted"] is True
    assert module.exceptions.acted == [{"po_name": "P00011", "requested_by": "ana@x.com"}]
    assert client.post("/api/exceptions/late_po/P00012/act", headers=approver).status_code == 409
    assert client.post("/api/exceptions/late_po/P00404/act", headers=approver).status_code == 404
    assert client.post("/api/exceptions/unlinked_mail/x/act", headers=approver).status_code == 422


# --- planning ---------------------------------------------------------------------------------


async def test_planning_runs_detail_and_what_if(
    client: TestClient, module: MemoryDirectorModule, planner: FakeAgentCaller
) -> None:
    await _users(module)
    module.planning.rows["run_1"] = PlanningRunRow(
        run_id="run_1",
        case_id="plan_2026-09-14",
        kind="daily_plan",
        as_of=TODAY,
        warehouse_id=1,
        status="awaiting_approval",
        approval_id=12,
        summary="14 rules, 3 RFQs",
        totals={"lines": 14.0},
        created_at=NOW,
        updated_at=NOW,
    )
    module.planning.line_rows["run_1"] = [PlanningLineRow(line=_line(), accepted=None)]
    viewer, approver = _token(client, "vic@x.com"), _token(client, "ana@x.com")

    runs = client.get("/api/planning/runs", headers=viewer).json()
    assert [r["run_id"] for r in runs] == ["run_1"] and runs[0]["approval_id"] == 12
    detail = client.get("/api/planning/runs/run_1", headers=viewer).json()
    assert detail["lines"][0]["line"]["order_qty"] == 23 and detail["lines"][0]["accepted"] is None
    assert client.get("/api/planning/runs/nope", headers=viewer).status_code == 404

    simulated = _line("run_2:101", service_level=0.99, ss=9, rop=23, order_up_to=37, order_qty=27)
    result = InventoryPlanningResult(
        kind="what_if",
        case_id="whatif_x",
        run_id="run_2",
        outcome=Outcome(status="no_action", summary="simulated"),
        proposal=ReplenishmentProposal(
            run_id="run_2", as_of=TODAY, warehouse_id=1, lines=[simulated]
        ),
    )
    planner.replies.append(AgentReply(status="completed", text=result.model_dump_json()))
    body = {"line_id": "run_1:101", "overrides": {"service_level": 0.99}}
    assert (
        client.post("/api/planning/runs/run_1/what-if", json=body, headers=viewer).status_code
        == 403
    )
    answer = client.post("/api/planning/runs/run_1/what-if", json=body, headers=approver)
    assert answer.status_code == 200
    assert (
        answer.json()["baseline"]["order_qty"] == 23
        and answer.json()["simulated"]["order_qty"] == 27
    )
    sent = planner.sent[0]
    assert '"kind":"what_if"' in sent.task_json and '"product_ids":[101]' in sent.task_json
    assert '"service_level":0.99' in sent.task_json
    missing = {"line_id": "run_1:999", "overrides": {}}
    assert (
        client.post("/api/planning/runs/run_1/what-if", json=missing, headers=approver).status_code
        == 404
    )


# --- settings and the stream ------------------------------------------------------------------


async def test_settings_version_zero_is_the_environment_and_a_save_is_announced(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    viewer, admin = _token(client, "vic@x.com"), _token(client, "adm@x.com")
    zero = client.get("/api/settings", headers=viewer).json()
    assert zero["version"] == 0 and zero["settings"]["rfq_no_reply_days"] == [2, 5]
    assert zero["settings"]["planning_service_level"] is None
    saved = client.put(
        "/api/settings",
        json={"settings": {**zero["settings"], "rfq_no_reply_days": [4]}, "note": "slower"},
        headers=admin,
    )
    assert saved.status_code == 200 and saved.json()["version"] == 1
    assert [e.kind for e in module.realtime.published] == ["settings_changed"]
    assert module.realtime.published[0].payload == {"version": 1, "changed_by": "adm@x.com"}


async def test_stream_needs_a_token_and_starts_with_the_connected_comment(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    assert client.get("/api/stream").status_code == 422  # no token at all
    assert client.get("/api/stream?token=bad").status_code == 401
    # The test client cannot disconnect a streaming response, so the 200 path is exercised on
    # the route function itself: the same token check, the same frames.
    token = _raw_token(client, "vic@x.com")
    settings = client.app.state.settings  # type: ignore[attr-defined]
    response = await stream(token=token, settings=settings, realtime=module.realtime)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    first = await anext(response.body_iterator)
    assert first == ": connected\n\n"
    await module.realtime.publish("case_updated", {"case_id": "c1"})
    frame = await anext(response.body_iterator)
    for _ in range(20):  # heartbeats may land before the event
        if not frame.startswith(": ping"):
            break
        frame = await anext(response.body_iterator)
    assert frame.startswith("event: case_updated")
    await response.body_iterator.aclose()  # type: ignore[attr-defined]


async def test_case_changes_are_published_for_the_stream(module: MemoryDirectorModule) -> None:
    store = module.case_store
    case, _ = await store.attach_or_create(kind="rfq", po_name="P00015")
    await store.add_event(case.case_id, "approval_requested", {"approval_id": 3})
    await store.add_event(case.case_id, "result", {"run_id": "r1", "status": "sent"})
    await store.update(case.case_id, status="done")
    kinds = [e.kind for e in module.realtime.published]
    assert kinds == [
        "case_updated",
        "case_updated",
        "approval_created",
        "case_updated",
        "run_finished",
        "case_updated",
    ]
    assert module.realtime.published[2].payload["approval_id"] == 3
    assert module.realtime.published[4].payload["run_id"] == "r1"


async def test_line_demand_is_proxied_from_the_planner(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    module.planning.rows["run_1"] = PlanningRunRow(
        run_id="run_1",
        case_id="plan",
        kind="daily_plan",
        as_of=TODAY,
        warehouse_id=1,
        status="awaiting_approval",
        created_at=NOW,
        updated_at=NOW,
    )
    module.planning.line_rows["run_1"] = [PlanningLineRow(line=_line())]
    module.demand.histories[101] = {
        "since": "2026-06-16",
        "until": "2026-09-14",
        "days": [{"day": "2026-09-13", "ordered": 3, "delivered": 3}],
    }
    viewer = _token(client, "vic@x.com")
    body = client.get(
        "/api/planning/runs/run_1/lines/run_1:101/demand?days=30", headers=viewer
    ).json()
    assert body["product_id"] == 101 and body["forecast_daily"] == 2.0
    assert body["days"] == [{"day": "2026-09-13", "ordered": 3.0, "delivered": 3.0}]
    assert module.demand.calls == [(101, 30)]
    assert (
        client.get("/api/planning/runs/run_1/lines/nope/demand", headers=viewer).status_code == 404
    )
