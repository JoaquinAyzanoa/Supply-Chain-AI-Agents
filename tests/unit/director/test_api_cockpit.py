"""The cockpit's data (phase 11 S8): Home KPIs, the AI performance page, Supplier 360,
push subscriptions with their relay, and bulk decisions in the inbox."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.api.planning import PlanningLineRow, PlanningRunRow
from director.autonomy import AutoAction
from director.learning import DecisionFeedback
from director.policies import PoFacts
from director.push import MemoryPushSender, PushRelay, PushSubscription
from director.testing import MemoryDirectorModule
from sc_core.app import create_application
from sc_core.app.realtime import RealtimeEvent
from sc_core.infra.settings import Settings
from sc_core.odoo.models import AgentRun, MailLink, PurchaseOrder, Ref, SupplierInfo
from sc_core.schema.planning import ReplenishmentLine
from sc_core.shared.time import local_today, utc_now

TODAY = local_today()
NOW = utc_now()


@pytest.fixture
def module() -> MemoryDirectorModule:
    return MemoryDirectorModule()


@pytest.fixture
def client(module: MemoryDirectorModule) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="director",
        environment="test",
        events={"signing_secret": SecretStr("events")},
        ui={
            "jwt_secret": SecretStr("ui"),
            "vapid_public_key": "BPUBLIC",
            "vapid_private_key": SecretStr("private"),
        },
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


def _po(po_id: int, name: str, partner: tuple[int, str], **over: object) -> PurchaseOrder:
    base: dict[str, object] = {
        "id": po_id,
        "name": name,
        "state": "purchase",
        "partner_id": Ref(id=partner[0], name=partner[1]),
        "date_order": datetime(TODAY.year, TODAY.month, 1, 9, tzinfo=UTC),
        "date_approve": datetime(TODAY.year, TODAY.month, 1, 10, tzinfo=UTC),
        "date_planned": datetime.combine(
            TODAY + timedelta(days=5), datetime.min.time(), tzinfo=UTC
        ),
        "amount_total": 1000.0,
        "currency_id": Ref(id=2, name="USD"),
        "receipt_status": "pending",
    }
    return PurchaseOrder(**{**base, **over})  # type: ignore[arg-type]


async def _the_desk(module: MemoryDirectorModule) -> None:
    hidraulica = (8, "Proveedor Hidraulica")
    module.board_orders.add(_po(77, "P00077", hidraulica, amount_total=2314.44))
    module.board_orders.add(
        _po(
            70,
            "P00070",
            hidraulica,
            amount_total=500.0,
            date_approve=datetime(TODAY.year, TODAY.month, 1, 10, tzinfo=UTC) - timedelta(days=10),
        )
    )
    module.board_orders.add(_po(81, "P00081", (9, "Hidraulica Alterna"), amount_total=4200.0))
    module.performance.rows.append(
        {
            "partner_id": 8,
            "partner_name": "Proveedor Hidraulica",
            "score": 75.5,
            "otif": 0.5,
            "promise_drift_days": 4.0,
            "lead_time_mean_days": 42.3,
        }
    )
    module.performance.rows.append(
        {"partner_id": 9, "partner_name": "Hidraulica Alterna", "score": 90.0, "otif": 0.9}
    )
    module.exceptions.facts.append(
        PoFacts(
            po_id=77,
            po_name="P00077",
            partner_id=8,
            state="purchase",
            date_planned=TODAY - timedelta(days=7),
            receipt_status="pending",
        )
    )
    module.approvals.seed(
        24,
        kind="award",
        summary="Round #1: award to Proveedor Hidraulica",
        payload={"facts": {"amount": 4200.0, "currency": "USD"}},
        po=(81, "P00081"),
    )
    module.approvals.seed(
        25,
        kind="vendor_bill",
        summary="Invoice F001-000201 for P00078 matches",
        payload={"verdict": "clean"},
        po=(78, "P00078"),
        status="approved",
    )
    # the seeded approvals were raised within the period
    for approval_id in (24, 25):
        module.approvals.rows[approval_id] = module.approvals.rows[approval_id].model_copy(
            update={"create_date": NOW - timedelta(days=2)}
        )
    module.auto_actions.add(
        AutoAction(
            id=1,
            created_at=NOW - timedelta(days=1),
            agent="supplier_comms",
            kind="send_email",
            level="auto_notice",
            rule_id="reminders",
            summary="Reminder sent to Proveedor Hidraulica for P00080",
            po_name="P00080",
        )
    )
    module.auto_actions.add(
        AutoAction(
            id=2,
            created_at=NOW - timedelta(days=2),
            agent="invoice_match",
            kind="vendor_bill",
            level="auto",
            rule_id="small-bills",
            summary="Bill F001-000202 recorded",
            po_name="P00079",
        )
    )
    await module.feedback.record(
        DecisionFeedback(
            approval_id=11,
            kind="po_change",
            status="approved",
            edited=True,
            seconds_to_decide=7200,
            resolved_at=NOW - timedelta(days=1),
        )
    )
    await module.feedback.record(
        DecisionFeedback(
            approval_id=12,
            kind="send_email",
            status="rejected",
            seconds_to_decide=1800,
            resolved_at=NOW - timedelta(days=1),
        )
    )
    module.runs.rows.extend(
        [
            AgentRun(
                id=1,
                run_id="run_a",
                agent="supplier_comms",
                case_id="case_1",
                status="sent",
                model="deepseek",
                started_at=NOW - timedelta(days=1),
                cost_usd=0.02,
            ),
            AgentRun(
                id=2,
                run_id="run_b",
                agent="sourcing",
                case_id="case_1",
                status="sent",
                model="deepseek",
                started_at=NOW - timedelta(days=1),
                cost_usd=0.06,
            ),
            AgentRun(
                id=3,
                run_id="run_c",
                agent="supplier_comms",
                case_id="case_2",
                status="sent",
                model="deepseek",
                started_at=NOW - timedelta(days=40),
                cost_usd=0.10,
            ),
        ]
    )


async def test_home_gathers_the_kpis_and_what_needs_you(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    await _the_desk(module)
    vic = _token(client, "vic@x.com")
    home = client.get("/api/home", headers=vic)
    assert home.status_code == 200
    body = home.json()
    kpis = {k["key"]: k for k in body["kpis"]}
    assert kpis["service_level"]["value"] == 70.0 and kpis["service_level"]["unit"] == "pct"
    assert kpis["late_orders"]["value"] == 1 and body["late"] == 1
    assert kpis["pending_approvals"]["value"] == 1
    assert kpis["spend_month"]["value"] == 6514.44 and kpis["spend_month"]["previous"] == 500.0
    assert kpis["spend_month"]["currency"] == "USD"
    assert kpis["ai_cost_month"]["value"] == 0.08 and kpis["ai_cost_month"]["previous"] == 0.1
    assert kpis["automated_week"]["value"] == 2
    assert [i["path"] for i in body["needs_you"]] == ["/approvals?id=24"]
    assert "4,200 USD" in body["needs_you"][0]["text"]


async def test_home_tiles_explain_themselves_in_the_language_people_read(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    await _the_desk(module)
    vic = _token(client, "vic@x.com")
    english = {k["key"]: k["detail"] for k in client.get("/api/home", headers=vic).json()["kpis"]}
    assert english["service_level"] == "2 scored supplier(s)"
    assert english["late_orders"].endswith("silent RFQ(s)")
    settings = client.app.state.injector.get(Settings)  # type: ignore[attr-defined]
    settings.agents.__dict__["language"] = "es"  # as SC__AGENTS__LANGUAGE=es would
    spanish = {k["key"]: k["detail"] for k in client.get("/api/home", headers=vic).json()["kpis"]}
    assert spanish["service_level"] == "2 proveedor(es) evaluado(s)"
    assert spanish["late_orders"].endswith("solicitud(es) sin respuesta")
    assert spanish["pending_approvals"].startswith("antigüedad mediana")


async def test_the_ai_page_measures_automation_decisions_predictions_and_cost(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    await _the_desk(module)
    module.planning.rows["run_1"] = PlanningRunRow(
        run_id="run_1",
        case_id="plan",
        kind="daily_plan",
        as_of=TODAY,
        warehouse_id=1,
        status="done",
        totals={},
        created_at=NOW,
        updated_at=NOW,
    )
    base = {
        "line_id": "run_1:1",
        "product_id": 1,
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
        "supplier_id": 8,
        "unit_price": 10.0,
        "currency": "USD",
        "action": "create_rfq",
    }
    module.planning.line_rows["run_1"] = [
        PlanningLineRow(line=ReplenishmentLine(**{**base, "wape": 0.12})),  # type: ignore[arg-type]
        PlanningLineRow(
            line=ReplenishmentLine(**{**base, "line_id": "run_1:2", "product_id": 2, "wape": 0.2})  # type: ignore[arg-type]
        ),
        PlanningLineRow(
            line=ReplenishmentLine(  # type: ignore[arg-type]
                **{**base, "line_id": "run_1:3", "product_id": 3, "abc_class": "C", "wape": None}
            )
        ),
    ]
    vic = _token(client, "vic@x.com")
    ai = client.get("/api/ai?days=30", headers=vic)
    assert ai.status_code == 200
    body = ai.json()
    automation = {a["kind"]: a for a in body["automation"]}
    assert automation["send_email"] == {
        "kind": "send_email",
        "automated": 1,
        "decided": 0,
        "rate": 1.0,
    }
    assert automation["vendor_bill"] == {
        "kind": "vendor_bill",
        "automated": 1,
        "decided": 1,
        "rate": 0.5,
    }
    assert automation["award"]["rate"] == 0.0
    assert body["automation_rate"] == 0.5 and body["decisions"] == 2
    assert body["turnaround_hours_median"] == 1.2  # median of 2 h and 0.5 h
    assert body["edit_rate"] == 1.0 and body["rejection_rate"] == 0.5
    assert body["eta_error_days"] == 4.0
    assert body["wape_by_class"] == [
        {"abc_class": "A", "wape": 0.16, "lines": 2},
        {"abc_class": "C", "wape": None, "lines": 1},
    ]
    assert body["invoices_first_time"] == 2 and body["invoices_total"] == 2
    assert body["invoices_first_time_rate"] == 1.0 and body["negotiation_savings"] is None
    assert body["runs"] == 2 and body["cost_usd"] == 0.08 and body["cases_with_runs"] == 1
    assert body["cost_per_case_usd"] == 0.08
    assert body["by_agent"][0] == {"agent": "sourcing", "runs": 1, "cost_usd": 0.06}


async def test_supplier_360_puts_the_scorecard_orders_prices_mails_and_ranking_together(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    await _the_desk(module)
    module.prices.rows[8] = [
        SupplierInfo(
            id=1,
            partner_id=Ref(id=8, name="Proveedor Hidraulica"),
            product_id=Ref(id=1, name="[CBEA-LHN] Valvula"),
            product_code="VC-1",
            price=104.16,
            currency_id=Ref(id=2, name="USD"),
            min_qty=1,
            delay=28,
        ),
        # Odoo's usual shape: the row sits on the template, so the variant is looked up
        SupplierInfo(
            id=2,
            partner_id=Ref(id=8, name="Proveedor Hidraulica"),
            product_tmpl_id=Ref(id=30, name="Manguera"),
            product_code="MG-2",
            price=12.5,
            currency_id=Ref(id=2, name="USD"),
            min_qty=10,
            delay=14,
        ),
    ]
    module.prices.variants[30] = 3
    module.mail_links.rows[77] = [
        MailLink(
            id=1,
            po_id=Ref(id=77, name="P00077"),
            direction="out",
            graph_message_id="AAMk-out",
            received_at=NOW - timedelta(days=3),
            web_link="https://outlook/out",
            confidence="exact",
        ),
        MailLink(
            id=2,
            po_id=Ref(id=77, name="P00077"),
            direction="in",
            graph_message_id="AAMk-in",
            received_at=NOW - timedelta(days=2),
            web_link="https://outlook/in",
            confidence="human",
        ),
    ]
    module.performance.rankings[1] = {
        "product_id": 1,
        "suppliers": [
            {"rank": 1, "partner_id": 9, "partner_name": "Hidraulica Alterna", "score": 90.0},
            {"rank": 2, "partner_id": 8, "partner_name": "Proveedor Hidraulica", "score": 75.5},
        ],
    }
    module.performance.rankings[3] = {
        "product_id": 3,
        "suppliers": [
            {"rank": 1, "partner_id": 8, "partner_name": "Proveedor Hidraulica", "score": 75.5}
        ],
    }
    module.sourcing_source.rows.append(
        {"id": 1, "case_id": "round_1", "status": "open", "rfqs": [{"partner_id": 8}]}
    )
    vic = _token(client, "vic@x.com")
    page = client.get("/api/suppliers/8", headers=vic)
    assert page.status_code == 200
    body = page.json()
    assert body["partner_name"] == "Proveedor Hidraulica" and body["score"]["score"] == 75.5
    assert [o["po_name"] for o in body["orders"]] == ["P00077", "P00070"]
    assert body["prices"][0]["product"] == "[CBEA-LHN] Valvula"
    assert body["prices"][0]["supplier_code"] == "VC-1" and body["prices"][0]["lead_days"] == 28
    assert [m["direction"] for m in body["emails"]] == ["in", "out"]  # newest first, no text
    assert body["emails"][0]["web_link"] == "https://outlook/in"
    assert body["products"] == [
        {
            "product_id": 1,
            "product": "[CBEA-LHN] Valvula",
            "rank": 2,
            "suppliers": 2,
            "best_partner_name": "Hidraulica Alterna",
            "best_score": 90.0,
        },
        {
            "product_id": 3,
            "product": "Manguera",
            "rank": 1,
            "suppliers": 1,
            "best_partner_name": "Proveedor Hidraulica",
            "best_score": 75.5,
        },
    ]
    assert body["prices"][1]["product_id"] == 3  # the template row, resolved to its variant
    assert [r["id"] for r in body["rounds"]] == [1]
    assert client.get("/api/suppliers/999", headers=vic).status_code == 404


async def test_push_subscriptions_are_kept_and_a_new_approval_reaches_every_phone(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    vic = _token(client, "vic@x.com")
    key = client.get("/api/push/key", headers=vic).json()
    assert key == {"enabled": True, "public_key": "BPUBLIC"}
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "p", "auth": "a"}}
    assert client.post("/api/push/subscriptions", json=sub, headers=vic).status_code == 201
    assert len(await module.push_store.all()) == 1

    module.approvals.seed(
        31,
        kind="send_email",
        summary="Send request for quotation to Proveedor Hidraulica for P00088",
    )
    sender = MemoryPushSender(gone={"https://push.example/dead"})
    await module.push_store.add(
        PushSubscription(endpoint="https://push.example/dead", p256dh="p", auth="a")
    )
    relay = PushRelay(
        realtime=module.realtime,
        store=module.push_store,
        sender=sender,
        approvals=module.approvals,
        control_tower_url="https://tower.test/",
    )
    event = RealtimeEvent(kind="approval_created", payload={"approval_id": 31}, at=NOW)
    assert await relay.handle(event) == 1
    [(endpoint, payload)] = sender.sent
    assert endpoint == "https://push.example/abc"
    assert payload == {
        "title": "Approval #31",
        "body": "Send request for quotation to Proveedor Hidraulica for P00088",
        "url": "https://tower.test/approvals?id=31",
        "tag": "approval-31",
    }
    # the dead endpoint is forgotten; other events are ignored
    assert [s.endpoint for s in await module.push_store.all()] == ["https://push.example/abc"]
    assert await relay.handle(RealtimeEvent(kind="case_updated", payload={}, at=NOW)) == 0
    assert (
        client.request(
            "DELETE", "/api/push/subscriptions", json={"endpoint": sub["endpoint"]}, headers=vic
        ).status_code
        == 204
    )
    assert await module.push_store.all() == []


async def test_bulk_decisions_resolve_each_pending_approval_and_report_the_rest(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    module.approvals.seed(
        41,
        kind="send_email",
        summary="Reminder to Proveedor Hidraulica for P00080",
        payload={"to": ["v@x.com"], "subject": "s", "html_body": "<p>x</p>"},
    )
    module.approvals.seed(
        42,
        kind="send_email",
        summary="ETA request to Proveedor Hidraulica for P00077",
        payload={"to": ["v@x.com"], "subject": "s", "html_body": "<p>x</p>"},
    )
    module.approvals.seed(43, kind="send_email", summary="already decided", status="approved")
    ana, vic = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    body = {"ids": [41, 42, 43, 99], "status": "approved"}
    assert client.post("/api/approvals/bulk", json=body, headers=vic).status_code == 403
    done = client.post("/api/approvals/bulk", json=body, headers=ana)
    assert done.status_code == 200
    results = {r["id"]: r for r in done.json()["results"]}
    assert results[41]["status"] == "approved" and results[42]["status"] == "approved"
    assert results[43]["error"] == "already approved" and results[99]["error"] == "not found"
    assert done.json()["resolved"] == 2
    assert {r["id"] for r in module.approvals.resolved} == {41, 42}
