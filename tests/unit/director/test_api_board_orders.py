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
from director.policies import PoFacts
from director.testing import MemoryDirectorModule
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.odoo.models import PurchaseOrder, Ref
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
    module.approvals.seed(31, kind="po_change", summary="date change on P00003", po=(3, "P00003"))
    module.approvals.seed(32, kind="escalation", summary="P00006 needs a person", po=(6, "P00006"))
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
    }
    assert board["counts"]["incoming"] == 2 and board["due_soon_days"] == 5
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
    silent = by_name["P00002"]
    assert silent["days_silent"] == 3 and silent["next_action"] == "follow_up"
    assert silent["last_outbound"] == (local_today() - timedelta(days=3)).isoformat()
    assert by_name["P00003"]["pending_approval"] == {
        "id": 31,
        "kind": "po_change",
        "summary": "date change on P00003",
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
