"""Demo mode: the scripted scenario runs end to end on the memory doubles.

The supplier's replies go to a memory mailbox, the world (receipt, bill) to a
memory Odoo, the agents are scripted; what the test checks is the script itself:
every step's outcome, what it sends, what it decides on the presenter's behalf,
what a reset puts back, and what a step says when the world has not answered.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.briefing import Paragraph
from director.demo import STEPS, DemoOrder
from director.testing import MemoryDirectorModule
from sc_core.a2a.protocol import AgentReply
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import InvitedRfq, Outcome, SourcingResult, SupplierCommsResult
from sc_core.shared.time import local_today

TODAY = local_today()


@pytest.fixture
def supplier() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def sourcing() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def module(
    supplier: FakeAgentCaller, sourcing: FakeAgentCaller, chat: ScriptedChatClient
) -> MemoryDirectorModule:
    return MemoryDirectorModule(supplier_comms=supplier, sourcing=sourcing, chat=chat)


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


def _supplier_reply(kind: str, status: str, approval_id: int | None = None) -> AgentReply:
    result = SupplierCommsResult(
        kind=kind,  # type: ignore[arg-type]
        case_id="demo_x",
        run_id="run_1",
        outcome=Outcome(status=status, summary=f"{kind} {status}", approval_id=approval_id),  # type: ignore[arg-type]
    )
    return AgentReply(
        status="input_required" if status == "awaiting_approval" else "completed",
        text=result.model_dump_json(),
    )


def _sourcing_reply(
    kind: str, status: str, approval_id: int | None = None, **extra: Any
) -> AgentReply:
    result = SourcingResult(
        kind=kind,  # type: ignore[arg-type]
        case_id="demo_x",
        run_id="run_s1",
        outcome=Outcome(status=status, summary=f"{kind} {status}", approval_id=approval_id),  # type: ignore[arg-type]
        **extra,
    )
    return AgentReply(
        status="input_required" if status == "awaiting_approval" else "completed",
        text=result.model_dump_json(),
    )


def _the_world(module: MemoryDirectorModule) -> None:
    module.demo_world.late = DemoOrder(
        po_id=77,
        po_name="P00077",
        partner_id=8,
        date_planned=TODAY - timedelta(days=4),
        amount_total=2314.44,
    )
    module.risk_source.report_data["products"] = [
        {
            "product_id": 5,
            "product": "[990-011-007] Kit de sellos",
            "p_stockout_30": 0.92,
            "suggested_qty": 12,
            "late_po_names": [],
            "open_po_names": [],
        },
        {
            "product_id": 6,
            "product": "[CBEA-LHN] Valvula",
            "p_stockout_30": 0.95,
            "suggested_qty": 4,
            "late_po_names": ["P00077"],
            "open_po_names": [],
        },
    ]


async def test_the_whole_script_runs_unattended_and_decides_as_the_presenter(
    client: TestClient,
    module: MemoryDirectorModule,
    supplier: FakeAgentCaller,
    sourcing: FakeAgentCaller,
    chat: ScriptedChatClient,
) -> None:
    await _users(module)
    _the_world(module)
    ana, vic = _token(client, "ana@x.com"), _token(client, "vic@x.com")

    # Nobody can step before a reset; viewers only watch.
    assert client.post("/api/demo/next", json={"approve": True}, headers=vic).status_code == 403
    assert client.post("/api/demo/next", json={"approve": True}, headers=ana).status_code == 409
    view = client.post("/api/demo/reset", headers=ana).json()
    assert view["position"] == 0 and view["next"]["key"] == "late_order_eta"
    assert [s["key"] for s in view["steps"]] == [s.key for s in STEPS]
    assert view["records"]["late_order"]["po_name"] == "P00077"
    assert (
        view["records"]["late_order"]["original_date_planned"]
        == (TODAY - timedelta(days=4)).isoformat()
    )
    receipt_po = view["records"]["receipt_order"]["po_name"]
    assert receipt_po == "P00901" and view["ready"] == {"mailbox": True, "world": True, "notes": []}

    # 1. the ETA request: the agent pauses on the email, the presenter approves it
    module.approvals.seed(
        201, kind="send_email", summary="Delivery date request", po=(77, "P00077")
    )
    supplier.replies.append(_supplier_reply("request_eta", "awaiting_approval", 201))
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 1
    done = view["outcomes"][-1]
    assert done["key"] == "late_order_eta" and done["status"] == "done"
    assert done["approval_ids"] == [201]
    assert {"label": "approval #201", "path": "/approvals?id=201"} in done["links"]
    assert (await module.approvals.get(201)).status == "approved"

    # 2. the supplier's reply: sent from its mailbox, linked by the subject token, the
    #    order change approved
    module.mailbox.report = {"status": "ok", "fetched": 1, "linked_po_names": ["P00077"]}
    module.approvals.seed(202, kind="po_change", summary="date change", po=(77, "P00077"))
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 2
    sent = module.supplier_mailbox.sent
    assert len(sent) == 1 and sent[0]["subject"] == "Re: [P00077] Delivery date"
    assert sent[0]["to"] == "scai.compras@outlook.com"
    assert (TODAY + timedelta(days=7)).isoformat() in sent[0]["text"]
    assert view["outcomes"][-1]["approval_ids"] == [202]
    assert (await module.approvals.get(202)).status == "approved"

    # 3. the risk radar: the product with nothing on order starts a quote round
    sourcing.replies.append(
        _sourcing_reply(
            "quote_round",
            "sent",
            round_id=7,
            invited=[
                InvitedRfq(partner_id=8, partner_name="Proveedor Hidraulica", po_name="P00091"),
                InvitedRfq(partner_id=9, partner_name="Hidraulica Alterna", po_name="P00092"),
            ],
        )
    )
    module.sourcing_source.rows.append(
        {
            "id": 7,
            "case_id": "round_case",
            "status": "open",
            "rfqs": [
                {"po_name": "P00091", "partner_id": 8},
                {"po_name": "P00092", "partner_id": 9},
            ],
        }
    )
    module.approvals.seed(203, kind="send_email", summary="RFQ", po=(91, "P00091"))
    module.approvals.seed(204, kind="send_email", summary="RFQ", po=(92, "P00092"))
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 3
    task = sourcing.sent[-1].task_json
    assert '"kind":"quote_round"' in task and '"product_id":5' in task and '"qty":12.0' in task
    assert view["records"]["supplier_rfq"] == "P00091"
    assert view["records"]["rfq_names"] == ["P00091", "P00092"]
    assert sorted(view["outcomes"][-1]["approval_ids"]) == [203, 204]
    assert {"label": "risk radar", "path": "/risk"} in view["outcomes"][-1]["links"]

    # 4. the quote above target: the reply carries prices 12% above the list, the
    #    counter-offer waits and is approved
    module.demo_world.lines["P00091"] = module.demo_world.lines["P00901"]
    # the supplier cannot quote a request that has not reached it
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 3 and view["outcomes"][-1]["status"] == "waiting"
    assert "has not left yet" in view["outcomes"][-1]["summary"] and len(sent) == 1
    module.demo_world.outbound["P00091"] = 1
    # the rival's request left too, so the rival quotes as well, in character
    module.demo_world.outbound["P00092"] = 1
    module.demo_world.lines["P00092"] = module.demo_world.lines["P00901"]
    module.demo_world.contacts["P00092"] = (
        "Hidráulica Alterna SAC",
        "ventas.hidraulica.sc+alterna@gmail.com",
    )
    module.mailbox.report = {"status": "ok", "fetched": 1, "linked_po_names": ["P00091"]}
    module.approvals.seed(
        205,
        kind="negotiation_offer",
        summary="counter-offer",
        payload={"offer": {"offered_price": 26.5}},
        po=(91, "P00091"),
    )
    sourcing.replies.append(_sourcing_reply("counter_offer", "awaiting_approval", 205))
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 4
    assert sent[1]["subject"] == "Re: [P00091] Quotation" and "USD 28.00" in sent[1]["text"]
    assert (
        sent[2]["subject"] == "Re: [P00092] Quotation"
        and sent[2]["from"] == "Hidráulica Alterna SAC"
    )
    assert "USD 25.00" in sent[2]["text"] and "Delivery time: 12 days" in sent[2]["text"]
    assert '"kind":"counter_offer"' in sourcing.sent[-1].task_json
    assert '"target_price":25.0' in sourcing.sent[-1].task_json  # the list price before the quote
    assert view["outcomes"][-1]["approval_ids"] == [205]
    assert (await module.approvals.get(205)).status == "approved"

    # 5. the supplier accepts once the counter-offer reached it
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 4 and "counter-offer has not left" in view["outcomes"][-1]["summary"]
    module.demo_world.outbound["P00091"] = 2
    # the acceptance names the counter-offer's price; the comparison ends in an award
    module.approvals.seed(206, kind="award", summary="award", po=(91, "P00091"))
    sourcing.replies.append(_sourcing_reply("compare_quotes", "awaiting_approval", 206))
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 5
    assert "USD 26.50" in sent[3]["text"]
    # the presenter can read what the suppliers wrote; nothing of it is stored
    emails = client.get("/api/demo/emails", headers=vic).json()
    assert [(e["step"], e["po_name"], e["from_name"]) for e in emails] == [
        ("supplier_eta_reply", "P00077", "Proveedor Hidraulica"),
        ("supplier_quote", "P00091", "Proveedor Hidraulica"),
        ("supplier_quote", "P00092", "Hidráulica Alterna SAC"),
        ("award", "P00091", "Proveedor Hidraulica"),
    ]
    assert emails[3]["text"] == sent[3]["text"] and emails[1]["text"] == sent[1]["text"]
    assert '"kind":"compare_quotes"' in sourcing.sent[-1].task_json
    assert '"round_id":7' in sourcing.sent[-1].task_json
    assert view["outcomes"][-1]["approval_ids"] == [206]

    # 6. the warehouse receives short; the logistics agent's report is approved
    module.approvals.seed(207, kind="send_email", summary="discrepancy", po=(901, receipt_po))
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 6
    assert module.demo_world.receipts == [(901, 0.8)]
    assert view["outcomes"][-1]["summary"].startswith("WH/IN/901: 8 of 10 units received")
    assert view["outcomes"][-1]["approval_ids"] == [207]

    # 7. accounting types the bill 3% above; the match waits and is accepted
    module.approvals.seed(208, kind="vendor_bill", summary="bill", po=(901, receipt_po))
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 7
    assert module.demo_world.bills[0][0] == 901 and module.demo_world.bills[0][2] == 3.0
    assert view["records"]["bill"]["ref"].startswith("DEMO-")
    assert view["outcomes"][-1]["approval_ids"] == [208]

    # 8. the briefing
    chat.responses.append(Paragraph(text="A busy demo day."))
    view = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert view["position"] == 8 and view["finished"] is True and view["next"] is None
    assert view["outcomes"][-1]["links"][0] == {"label": "briefing", "path": "/briefing"}
    assert client.get("/api/briefing", headers=vic).json()["paragraph"] == "A busy demo day."

    # Finished: another next changes nothing; every approval the run made is remembered.
    again = client.post("/api/demo/next", json={"approve": True}, headers=ana).json()
    assert again["position"] == 8
    assert sorted(a for o in again["outcomes"] for a in o["approval_ids"]) == list(range(201, 209))
    assert [o["status"] for o in again["outcomes"]] == ["done"] * 8

    # A reset puts the world back: the late order's date, the round's RFQs, a new order
    # for the receipt (a received one cannot be un-received), pending approvals rejected.
    module.approvals.seed(209, kind="send_email", summary="left over", po=(91, "P00091"))
    view = client.post("/api/demo/reset", headers=ana).json()
    assert view["position"] == 0 and view["outcomes"] == []
    assert module.demo_world.cancelled == ["P00091", "P00092"]
    assert module.demo_world.planned[77] == TODAY - timedelta(days=4)
    assert view["records"]["receipt_order"]["po_name"] == "P00902"
    assert (
        view["records"]["late_order"]["original_date_planned"]
        == (TODAY - timedelta(days=4)).isoformat()
    )
    assert (await module.approvals.get(209)).status == "pending"  # not this run's


async def test_a_step_waits_without_resending_when_the_mailbox_is_slow(
    client: TestClient, module: MemoryDirectorModule, supplier: FakeAgentCaller
) -> None:
    await _users(module)
    _the_world(module)
    ana = _token(client, "ana@x.com")
    client.post("/api/demo/reset", headers=ana)
    # a rule let the request go alone: nothing to decide
    supplier.replies.append(_supplier_reply("request_eta", "sent"))
    view = client.post("/api/demo/next", json={"approve": False}, headers=ana).json()
    assert view["position"] == 1 and view["outcomes"][-1]["approval_ids"] == []

    # The reply is sent once; while the mailbox has not linked it the step waits.
    module.mailbox.report = {"status": "ok", "fetched": 0, "linked_po_names": []}
    view = client.post("/api/demo/next", json={"approve": False}, headers=ana).json()
    assert view["position"] == 1
    waiting = view["outcomes"][-1]
    assert waiting["key"] == "supplier_eta_reply" and waiting["status"] == "waiting"
    assert "run the step again" in waiting["summary"]
    assert len(module.supplier_mailbox.sent) == 1
    assert len(module.mailbox.calls) == 3  # three looks, then it gives the turn back

    # Linked but not yet proposed: still waiting, still one email.
    module.mailbox.report = {"status": "ok", "fetched": 1, "linked_po_names": ["P00077"]}
    view = client.post("/api/demo/next", json={"approve": False}, headers=ana).json()
    assert view["position"] == 1 and view["outcomes"][-1]["status"] == "waiting"
    assert len(module.supplier_mailbox.sent) == 1

    # The agent proposed the change: done, and without ``approve`` the decision is theirs.
    module.approvals.seed(202, kind="po_change", summary="date change", po=(77, "P00077"))
    view = client.post("/api/demo/next", json={"approve": False}, headers=ana).json()
    assert view["position"] == 2 and view["outcomes"][-1]["status"] == "done"
    assert view["outcomes"][-1]["approval_ids"] == [202]
    assert (await module.approvals.get(202)).status == "pending"
    assert [o["key"] for o in view["outcomes"]] == ["late_order_eta", "supplier_eta_reply"]

    # A step can be run again by name; the world says when it cannot play its part.
    module.demo_world.can_receive = False
    view = client.post(
        "/api/demo/next", json={"approve": False, "step": "short_receipt"}, headers=ana
    ).json()
    assert view["position"] == 2  # an out-of-turn step never moves the position
    failed = view["outcomes"][-1]
    assert failed["key"] == "short_receipt" and failed["status"] == "failed"
    assert "SC__DEMO__ODOO_LOGIN" in failed["summary"]
    assert client.get("/api/demo", headers=ana).json()["ready"]["world"] is False


async def test_a_kind_can_be_left_for_the_presenter_to_decide_on_screen(
    client: TestClient, module: MemoryDirectorModule, supplier: FakeAgentCaller
) -> None:
    await _users(module)
    _the_world(module)
    ana = _token(client, "ana@x.com")
    client.post("/api/demo/reset", headers=ana)
    module.approvals.seed(
        201, kind="send_email", summary="Delivery date request", po=(77, "P00077")
    )
    supplier.replies.append(_supplier_reply("request_eta", "awaiting_approval", 201))
    view = client.post(
        "/api/demo/next", json={"approve": True, "leave": ["send_email"]}, headers=ana
    ).json()
    assert view["position"] == 1 and view["outcomes"][-1]["approval_ids"] == [201]
    assert (await module.approvals.get(201)).status == "pending"  # theirs to click


async def test_the_demo_suppliers_write_in_spanish_when_the_demo_is_spanish(
    client: TestClient, module: MemoryDirectorModule, supplier: FakeAgentCaller
) -> None:
    await _users(module)
    _the_world(module)
    module.settings.demo.__dict__["language"] = "es"  # as SC__DEMO__LANGUAGE=es would
    ana = _token(client, "ana@x.com")
    client.post("/api/demo/reset", headers=ana)
    supplier.replies.append(_supplier_reply("request_eta", "sent"))
    client.post("/api/demo/next", json={"approve": False}, headers=ana)
    module.mailbox.report = {"status": "ok", "fetched": 1, "linked_po_names": ["P00077"]}
    module.approvals.seed(202, kind="po_change", summary="cambio de fecha", po=(77, "P00077"))
    view = client.post("/api/demo/next", json={"approve": False}, headers=ana).json()
    assert view["position"] == 2
    [sent] = module.supplier_mailbox.sent
    assert sent["subject"] == "Re: [P00077] Fecha de entrega"
    when = TODAY + timedelta(days=7)
    assert "Nueva fecha de entrega confirmada en su almacén" in sent["text"]
    assert f"({when.isoformat()})" in sent["text"] and " de " in sent["text"]
    [email] = client.get("/api/demo/emails", headers=ana).json()
    assert email["subject"] == sent["subject"] and email["text"] == sent["text"]
