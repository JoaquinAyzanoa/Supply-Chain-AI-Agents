"""The board: one card per order in the right column, and the moves Odoo allows."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.api.planning import PlanningLineRow, PlanningRunRow
from director.policies import PoFacts
from director.testing import MemoryDirectorModule
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.odoo.models import PurchaseOrder, PurchaseOrderLine, Ref
from sc_core.schema.planning import ReplenishmentLine
from sc_core.shared.time import local_today

from .helpers import agent_reply


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


def _po(
    po_id: int, name: str, state: str, *, planned_in: int | None = None, **over: Any
) -> PurchaseOrder:
    planned = (
        datetime.combine(
            local_today() + timedelta(days=planned_in), datetime.min.time(), tzinfo=UTC
        )
        if planned_in is not None
        else None
    )
    base: dict[str, Any] = {
        "id": po_id,
        "name": name,
        "state": state,
        "partner_id": Ref(id=7, name="Proveedor Hidraulica"),
        "user_id": Ref(id=2, name="Mitchell Admin"),
        "amount_total": 1200.5,
        "currency_id": Ref(id=1, name="USD"),
        "date_planned": planned,
        "receipt_status": "pending" if state == "purchase" else None,
    }
    return PurchaseOrder(**{**base, **over})


async def _seed(module: MemoryDirectorModule) -> None:
    await _users(module)
    today = local_today()
    b = module.board_orders
    b.add(_po(1, "P00001", "draft"))
    b.add(_po(2, "P00002", "sent"))
    b.add(_po(3, "P00003", "sent"))  # supplier answered: a po_change is pending
    b.add(_po(4, "P00004", "purchase", planned_in=20))
    b.add(_po(5, "P00005", "purchase", planned_in=3))
    b.add(_po(6, "P00006", "purchase", planned_in=-4, sc_eta_source="supplier"))
    b.add(_po(7, "P00007", "purchase", planned_in=-1, receipt_status="full"))
    b.add(_po(8, "P00008", "done"))
    b.add(
        _po(
            9,
            "P00009",
            "purchase",
            planned_in=-10,
            receipt_status="full",
            invoice_status="invoiced",
        )
    )
    b.add(
        _po(
            10,
            "P00010",
            "purchase",
            planned_in=-9,
            receipt_status="full",
            invoice_status="to invoice",
        )
    )
    module.approvals.seed(
        34, kind="vendor_bill", summary="Record invoice F001-000123", po=(10, "P00010")
    )
    module.approvals.seed(
        35,
        kind="send_email",
        summary="Report receipt discrepancies",
        po=(7, "P00007"),
        requested_by="logistics",
    )
    b.add(_po(11, "P00011", "purchase", planned_in=-400, receipt_status="full"))
    b.off_board.add(11)  # received a year ago: off the board unless someone must decide on it
    module.approvals.seed(
        36, kind="vendor_bill", summary="Record invoice F001-000900", po=(11, "P00011")
    )
    module.approvals.seed(31, kind="po_change", summary="date change on P00003", po=(3, "P00003"))
    module.approvals.seed(32, kind="escalation", summary="P00006 needs a person", po=(6, "P00006"))
    module.approvals.seed(
        33,
        kind="planning_run",
        summary="Plan 2026-09-14: 2 RFQs",
        payload={"run_id": "run_9", "as_of": "2026-09-14"},
        po=None,
    )
    module.mail_activity.known["P00002"] = (today - timedelta(days=3), None)
    module.exceptions.facts = [
        PoFacts(
            po_id=2,
            po_name="P00002",
            partner_id=7,
            state="sent",
            last_outbound_at=today - timedelta(days=3),
        ),
        PoFacts(
            po_id=6,
            po_name="P00006",
            partner_id=7,
            state="purchase",
            receipt_status="pending",
            date_planned=today - timedelta(days=4),
        ),
    ]
    case, _ = await module.cases.attach_or_create(kind="eta", po_name="P00006")
    await module.cases.update(case.case_id, status="escalated", summary="4 days late")
    closed, _ = await module.cases.attach_or_create(kind="rfq", po_name="P00004")
    await module.cases.update(closed.case_id, status="done", summary="quotation accepted")


async def test_board_puts_every_order_in_its_column_with_colours_and_badges(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _seed(module)
    board = client.get("/api/board", headers=_token(client, "vic@x.com")).json()
    by_name = {c["po_name"]: c for c in board["cards"]}
    assert {n: c["column"] for n, c in by_name.items()} == {
        "P00001": "proposed",
        "P00002": "rfq_sent",
        "P00003": "quote_received",
        "P00004": "confirmed",
        "P00005": "incoming",
        "P00006": "incoming",
        "P00007": "received",
        "P00008": "closed",
        "P00009": "invoicing",
        "P00010": "invoicing",
        "P00011": "invoicing",
    }
    assert board["counts"]["incoming"] == 2 and board["due_soon_days"] == 5
    assert board["counts"]["invoicing"] == 3
    assert by_name["P00007"]["discrepancy"] is True and by_name["P00009"]["discrepancy"] is False
    assert by_name["P00010"]["pending_approval"]["kind"] == "vendor_bill"
    assert by_name["P00007"]["pending_approval"]["requested_by"] == "logistics"
    assert (
        by_name["P00005"]["delivery"] == "due_soon" and by_name["P00004"]["delivery"] == "on_time"
    )
    late = by_name["P00006"]
    assert late["delivery"] == "late" and late["days_late"] == 4 and late["escalated"] is True
    assert (
        late["supplier_confirmed"] is True
        and late["case_code"]
        and late["summary"] == "4 days late"
    )
    assert late["pending_approval"]["kind"] == "escalation"
    assert late["act_kind"] == "late_po" and late["can_act"] is True
    assert board["planning"] == {
        "approval_id": 33,
        "run_id": "run_9",
        "as_of": "2026-09-14",
        "summary": "Plan 2026-09-14: 2 RFQs",
    }
    silent = by_name["P00002"]
    assert silent["days_silent"] == 3 and silent["next_action"] == "follow_up"
    assert silent["act_kind"] == "rfq_no_reply" and silent["can_act"] is True
    assert by_name["P00004"]["act_kind"] is None and by_name["P00004"]["can_act"] is False
    # a closed case still tells the story in the panel, without flags
    assert (
        by_name["P00004"]["case_status"] == "done"
        and by_name["P00004"]["summary"] == "quotation accepted"
    )
    assert by_name["P00004"]["escalated"] is False and by_name["P00004"]["on_hold_until"] is None
    assert silent["last_outbound"] == (local_today() - timedelta(days=3)).isoformat()
    assert by_name["P00003"]["pending_approval"] == {
        "id": 31,
        "kind": "po_change",
        "summary": "date change on P00003",
        "requested_by": "supplier_comms",
    }
    assert by_name["P00001"]["odoo_url"] == "http://odoo.test:8069/odoo/purchase.order/1"
    assert board["cards"][-1]["po_name"] == "P00008"  # closed last; late first within a column
    assert [c["po_name"] for c in board["cards"] if c["column"] == "incoming"] == [
        "P00006",
        "P00005",
    ]


async def test_moves_confirm_cancel_close_and_refuse_the_rest(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _seed(module)
    approver, viewer = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    assert (
        client.post("/api/board/P00002/move", json={"to": "confirmed"}, headers=viewer).status_code
        == 403
    )

    confirmed = client.post("/api/board/P00002/move", json={"to": "confirmed"}, headers=approver)
    assert confirmed.status_code == 200 and "confirmed" in confirmed.json()["message"]
    assert ("confirm", 2, None) in module.board_orders.actions
    assert module.board_orders.notes[-1][0] == 2 and "Ana" in module.board_orders.notes[-1][1]

    cancelled = client.post(
        "/api/board/P00001/move", json={"to": "closed", "note": "duplicate"}, headers=approver
    )
    assert cancelled.status_code == 200 and ("cancel", 1, None) in module.board_orders.actions
    assert "duplicate" in module.board_orders.notes[-1][1]

    done = client.post("/api/board/P00007/move", json={"to": "closed"}, headers=approver)
    assert done.status_code == 200 and ("done", 7, None) in module.board_orders.actions

    refused = client.post("/api/board/P00004/move", json={"to": "closed"}, headers=approver)
    assert refused.status_code == 422 and "warehouse" in refused.json()["detail"]
    assert (
        client.post("/api/board/P00004/move", json={"to": "received"}, headers=approver).status_code
        == 422
    )
    assert (
        client.post("/api/board/P00404/move", json={"to": "closed"}, headers=approver).status_code
        == 404
    )
    assert (
        client.post("/api/board/P00004/move", json={"to": "confirmed"}, headers=approver).json()[
            "message"
        ]
        == "already there"
    )


async def test_sending_a_proposal_goes_through_the_supplier_agent(
    client: TestClient, module: MemoryDirectorModule, supplier: FakeAgentCaller
) -> None:
    await _seed(module)
    approver = _token(client, "ana@x.com")
    supplier.replies.append(
        agent_reply(
            "send_rfq",
            "board_x",
            "awaiting_approval",
            "RFQ drafted, waiting for approval",
            approval_id=40,
            po_name="P00001",
        )
    )
    sent = client.post("/api/board/P00001/move", json={"to": "rfq_sent"}, headers=approver)
    assert sent.status_code == 200 and sent.json()["message"] == "RFQ drafted, waiting for approval"
    [task] = supplier.sent
    assert '"kind":"send_rfq"' in task.task_json and '"po_name":"P00001"' in task.task_json
    cases = await module.cases.open_for_po("P00001")
    assert cases and cases[0].status == "awaiting_approval"


async def test_supplier_confirmed_is_a_manual_mark(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _seed(module)
    approver = _token(client, "ana@x.com")
    marked = client.post(
        "/api/board/P00004/supplier-confirmed", json={"value": True}, headers=approver
    )
    assert (
        marked.status_code == 200 and ("supplier_confirmed", 4, True) in module.board_orders.actions
    )
    board = client.get("/api/board", headers=approver).json()
    assert next(c for c in board["cards"] if c["po_name"] == "P00004")["supplier_confirmed"] is True


def _line(
    po: PurchaseOrder, line_id: int, product_id: int, name: str, qty: float
) -> PurchaseOrderLine:
    return PurchaseOrderLine(
        id=line_id,
        order_id=Ref(id=po.id, name=po.name),
        name=name,
        product_id=Ref(id=product_id, name=name),
        product_qty=qty,
        price_unit=104.16,
        price_subtotal=qty * 104.16,
        qty_received=0.0,
        date_planned=po.date_planned,
    )


async def test_order_detail_shows_lines_and_where_the_order_came_from(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    viewer = _token(client, "vic@x.com")
    b = module.board_orders
    planned = b.add(
        _po(
            21,
            "P00021",
            "draft",
            planned_in=20,
            sc_external_ref="plan:run_1:7:1",
            origin="Plan 2026-09-13",
        )
    )
    typed = b.add(_po(22, "P00022", "draft", planned_in=15, origin="SEED/RFQ"))
    b.lines_by_po[21] = [
        _line(planned, 1, 101, "[CBEA-LHN] Válvula", 12),
        _line(planned, 2, 102, "[CXDA-XCN] Check", 40),
    ]
    b.lines_by_po[22] = [_line(typed, 3, 103, "[RDDA-LAN] Alivio", 14)]
    now = datetime.now(UTC)
    module.planning.rows["run_1"] = PlanningRunRow(
        run_id="run_1",
        case_id="plan_1",
        kind="daily_plan",
        as_of=local_today(),
        warehouse_id=1,
        status="awaiting_approval",
        approval_id=3,
        summary="17 RFQs, 30 rules",
        created_at=now,
        updated_at=now,
    )
    base: dict[str, Any] = {
        "warehouse_id": 1,
        "on_hand": 10,
        "incoming": 0,
        "position": 10,
        "forecast_daily": 2.0,
        "forecast_method": "ses",
        "sigma_daily": 0.5,
        "history_periods": 52,
        "lead_time_days": 30,
        "sigma_lead_time_days": 7.5,
        "service_level": 0.95,
        "review_period_days": 7,
        "abc_class": "A",
        "ss": 5,
        "rop": 19,
        "order_up_to": 33,
        "coverage_days": 5,
        "proposed_min": 19,
        "proposed_max": 33,
        "order_qty": 12,
        "action": "create_rfq",
    }
    module.planning.line_rows["run_1"] = [
        PlanningLineRow(
            line=ReplenishmentLine(
                line_id="run_1:101",
                product_id=101,
                product_ref="CBEA-LHN",
                explanation="Below the reorder point with 11 on hand.",
                **base,
            )
        ),
        PlanningLineRow(
            line=ReplenishmentLine(
                line_id="run_1:102", product_id=102, product_ref="CXDA-XCN", **base
            )
        ),
        PlanningLineRow(
            line=ReplenishmentLine(
                line_id="run_1:999",
                product_id=999,
                product_ref="OTHER",
                explanation="not on this order",
                **base,
            )
        ),
    ]

    detail = client.get("/api/board/P00021/detail", headers=viewer).json()
    assert [ln["product"] for ln in detail["lines"]] == ["[CBEA-LHN] Válvula", "[CXDA-XCN] Check"]
    assert detail["lines"][0]["qty"] == 12 and detail["lines"][0]["subtotal"] == 12 * 104.16
    assert detail["origin"]["kind"] == "planning" and detail["origin"]["run_id"] == "run_1"
    assert detail["origin"]["summary"] == "17 RFQs, 30 rules"
    assert detail["origin"]["explanations"] == [
        "CBEA-LHN: Below the reorder point with 11 on hand."
    ]

    typed_detail = client.get("/api/board/P00022/detail", headers=viewer).json()
    assert typed_detail["origin"] == {
        "kind": "odoo",
        "run_id": None,
        "as_of": None,
        "summary": None,
        "explanations": [],
        "created_by": "Mitchell Admin",
        "origin": "SEED/RFQ",
    }
    assert client.get("/api/board/P09999/detail", headers=viewer).status_code == 404
