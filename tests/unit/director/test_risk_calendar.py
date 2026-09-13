"""The risk radar, the demand calendar and the portfolio what-if on the Control Tower API."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.api.planning import PlanningLineRow, PlanningRunRow, portfolio_totals
from director.testing import MemoryDirectorModule
from sc_core.a2a import AgentReply
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.schema.a2a import InventoryPlanningResult, Outcome, SourcingResult
from sc_core.schema.planning import ReplenishmentLine, ReplenishmentProposal

TODAY = date(2026, 9, 14)
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def sourcing() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def planner() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def module(sourcing: FakeAgentCaller, planner: FakeAgentCaller) -> MemoryDirectorModule:
    return MemoryDirectorModule(sourcing=sourcing, inventory_planning=planner)


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


def _sourcing_reply(kind: str, status: str, **extra: Any) -> AgentReply:
    result = SourcingResult(
        kind=kind,  # type: ignore[arg-type]
        case_id="risk_x",
        run_id="run_s1",
        outcome=Outcome(status=status, summary=f"{kind} {status}"),  # type: ignore[arg-type]
        **extra,
    )
    return AgentReply(
        status="input_required" if status == "awaiting_approval" else "completed",
        text=result.model_dump_json(),
    )


def _product(product_id: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_id": product_id,
        "product_ref": f"HYD-{product_id}",
        "product_name": "Bomba",
        "on_hand": 4,
        "reserved": 0,
        "position": 4,
        "incoming_30": 0,
        "incoming_60": 10,
        "daily_mean": 0.5,
        "daily_sigma": 0.3,
        "forecast_method": "tsb",
        "days_of_cover": 8,
        "p_stockout_30": 0.62,
        "p_stockout_60": 0.8,
        "open_po_names": [],
        "late_po_names": [],
        "suggested_qty": 12,
        "exposure": 500.0,
        "unit_price": 40.0,
    }
    return {**base, **over}


async def test_the_radar_is_read_and_one_click_starts_the_right_sourcing_move(
    client: TestClient, module: MemoryDirectorModule, sourcing: FakeAgentCaller
) -> None:
    await _users(module)
    ana, vic = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    module.risk_source.report_data.update(
        products=[
            _product(101, open_po_names=["P00080"], late_po_names=["P00080"]),
            _product(102, p_stockout_30=0.55, suggested_qty=30),
            _product(103, open_po_names=["P00074"]),
        ],
        at_risk_30=3,
        cash_exposure=1000.0,
    )
    radar = client.get("/api/risk", headers=vic).json()
    assert radar["at_risk_30"] == 3
    assert [p["product_id"] for p in radar["products"]] == [101, 102, 103]

    # acting needs an approver and a product on the radar
    assert client.post("/api/risk/101/act", json={}, headers=vic).status_code == 403
    assert client.post("/api/risk/999/act", json={}, headers=ana).status_code == 404

    # a late order behind the risk: source the same line elsewhere
    sourcing.replies.append(_sourcing_reply("alternate_source", "awaiting_approval"))
    first = client.post("/api/risk/101/act", json={}, headers=ana)
    assert first.status_code == 202 and first.json()["status"] == "awaiting_approval"
    task = json.loads(sourcing.sent[0].task_json)
    assert task["kind"] == "alternate_source" and task["po_name"] == "P00080"
    assert task["product_id"] == 101 and task["case_id"].startswith("risk")
    assert "62%" in task["reason"] and "ana@x.com" in task["reason"]

    # no order to blame: a quote round for the suggested (or the given) quantity
    sourcing.replies.append(_sourcing_reply("quote_round", "sent", round_id=3))
    second = client.post("/api/risk/102/act", json={"qty": 40}, headers=ana)
    assert second.status_code == 202 and second.json()["status"] == "sent"
    task = json.loads(sourcing.sent[1].task_json)
    assert task["kind"] == "quote_round" and task["qty"] == 40 and task["product_id"] == 102
    sourcing.replies.append(_sourcing_reply("quote_round", "sent", round_id=4))
    client.post("/api/risk/102/act", json={}, headers=ana)
    assert json.loads(sourcing.sent[2].task_json)["qty"] == 30

    # an open order that is not late yet but will not cover the demand: re-source it too
    sourcing.replies.append(_sourcing_reply("alternate_source", "awaiting_approval"))
    assert client.post("/api/risk/103/act", json={}, headers=ana).status_code == 202
    task = json.loads(sourcing.sent[3].task_json)
    assert task["kind"] == "alternate_source" and task["po_name"] == "P00074"


async def test_the_demand_calendar_is_kept_by_approvers_and_read_by_everyone(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    ana, vic = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    promo = {
        "kind": "promotion",
        "name": "Feria",
        "start_date": "2026-10-01",
        "end_date": "2026-10-07",
        "factor": 1.8,
    }
    assert client.post("/api/planning/calendar", json=promo, headers=vic).status_code == 403
    created = client.post("/api/planning/calendar", json=promo, headers=ana)
    assert created.status_code == 201
    assert created.json()["id"] == 1 and created.json()["created_by"] == "ana@x.com"
    # the dates must make sense and a project names its product and quantity
    backwards = {**promo, "end_date": "2026-09-01"}
    assert client.post("/api/planning/calendar", json=backwards, headers=ana).status_code == 422
    project = {**promo, "kind": "project", "name": "Planta"}
    assert client.post("/api/planning/calendar", json=project, headers=ana).status_code == 422
    project.update(product_id=101, quantity=70)
    assert client.post("/api/planning/calendar", json=project, headers=ana).status_code == 201

    listed = client.get("/api/planning/calendar", headers=vic).json()
    assert [e["name"] for e in listed] == ["Feria", "Planta"]
    assert client.get("/api/planning/calendar?since=2026-10-08", headers=vic).json() == []

    assert client.delete("/api/planning/calendar/9", headers=ana).status_code == 404
    assert client.delete("/api/planning/calendar/1", headers=vic).status_code == 403
    assert client.delete("/api/planning/calendar/1", headers=ana).status_code == 204
    assert [e["id"] for e in client.get("/api/planning/calendar", headers=vic).json()] == [2]


def _line(line_id: str, **over: Any) -> ReplenishmentLine:
    base: dict[str, Any] = {
        "line_id": line_id,
        "product_id": int(line_id.split(":")[1]),
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


def test_portfolio_totals_count_orders_spend_stock_value_and_expected_stockouts() -> None:
    lines = [_line("r:1"), _line("r:2", order_qty=0, action="none", position=200, order_up_to=20)]
    totals = portfolio_totals(lines)
    assert totals.lines == 2 and totals.orders == 1 and totals.spend == 230.0
    assert totals.stock_value == 530.0 and totals.service_level == 0.95
    # 10 on hand against 60 expected in 30 days: nearly certain; 200 on hand: none
    assert 0.99 < totals.expected_stockouts <= 1.0
    assert portfolio_totals([]).service_level is None


async def test_a_class_what_if_compares_the_portfolio_before_and_after(
    client: TestClient, module: MemoryDirectorModule, planner: FakeAgentCaller
) -> None:
    await _users(module)
    ana, vic = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    module.planning.rows["run_1"] = PlanningRunRow(
        run_id="run_1",
        case_id="plan_2026-09-14",
        kind="daily_plan",
        as_of=TODAY,
        warehouse_id=1,
        status="awaiting_approval",
        approval_id=12,
        summary="2 lines",
        totals={"lines": 2.0},
        created_at=NOW,
        updated_at=NOW,
    )
    module.planning.line_rows["run_1"] = [
        PlanningLineRow(line=_line("run_1:101")),
        PlanningLineRow(line=_line("run_1:102", abc_class="B", order_qty=0, action="none")),
    ]
    simulated = ReplenishmentProposal(
        run_id="run_2",
        as_of=TODAY,
        warehouse_id=1,
        lines=[_line("run_2:101", service_level=0.99, order_up_to=37, order_qty=27)],
    )
    result = InventoryPlanningResult(
        kind="what_if",
        case_id="whatif_x",
        run_id="run_2",
        outcome=Outcome(status="no_action", summary="simulated"),
        proposal=simulated,
    )
    planner.replies.append(AgentReply(status="completed", text=result.model_dump_json()))
    body = {"abc_class": "A", "overrides": {"service_level": 0.99}}
    url = "/api/planning/runs/run_1/what-if-class"
    assert client.post(url, json=body, headers=vic).status_code == 403
    assert (
        client.post("/api/planning/runs/nope/what-if-class", json=body, headers=ana).status_code
        == 404
    )
    no_lines = {**body, "abc_class": "C"}
    assert client.post(url, json=no_lines, headers=ana).status_code == 404

    answer = client.post(url, json=body, headers=ana)
    assert answer.status_code == 200
    data = answer.json()
    assert data["abc_class"] == "A" and data["run_id"] == "run_2"
    assert data["baseline"]["lines"] == 1 and data["baseline"]["spend"] == 230.0
    assert data["simulated"]["spend"] == 270.0 and data["simulated"]["service_level"] == 0.99
    sent = json.loads(planner.sent[0].task_json)
    assert sent["kind"] == "what_if" and sent["product_ids"] == [101]  # only class A
    assert sent["overrides"]["service_level"] == 0.99
