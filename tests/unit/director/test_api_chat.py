"""Talking to the director about a case: answers from the facts, actions only on confirmation."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.api.chat import AssistantReply, EmailSnapshot, ProposedAction
from director.testing import MemoryDirectorModule
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.llm.testing import ScriptedChatClient
from sc_core.odoo.models import PurchaseOrder, Ref

from .helpers import agent_reply


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def supplier() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def module(chat: ScriptedChatClient, supplier: FakeAgentCaller) -> MemoryDirectorModule:
    return MemoryDirectorModule(supplier_comms=supplier, chat=chat)


@pytest.fixture
def client(module: MemoryDirectorModule) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        service_name="director",
        environment="test",
        events={"signing_secret": SecretStr("events")},
        ui={"jwt_secret": SecretStr("ui")},
        langfuse={"enabled": False},
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


async def _escalated_case(module: MemoryDirectorModule) -> str:
    case, _ = await module.cases.attach_or_create(kind="eta", po_name="P00066", partner_id=7)
    await module.cases.add_event(
        case.case_id, "rule_fired", {"rule": "po_late_escalate", "reason": "7 days late", "days": 7}
    )
    await module.cases.add_event(
        case.case_id, "escalated", {"reason": "7 days late", "approval_id": 21}
    )
    await module.cases.update(case.case_id, status="escalated", summary="7 days late, no ETA")
    module.approvals.seed(
        21,
        kind="escalation",
        summary="P00066 needs a person",
        po=(66, "P00066"),
        thread_id=case.case_id,
    )
    module.approvals.seed(
        19,
        kind="send_email",
        summary="Enviar solicitud de fecha de entrega a Proveedor Hidraulica por P00066",
        po=(66, "P00066"),
        thread_id="chat_earlier",
        payload={
            "to": ["ventas.hidraulica.sc@gmail.com"],
            "subject": "[P00066] Fecha de entrega",
            "html_body": "<p>Estimado proveedor: <b>¿dónde está la mercadería?</b></p>",
        },
    )
    module.orders_lookup.orders["P00066"] = PurchaseOrder(
        id=66,
        name="P00066",
        state="purchase",
        partner_id=Ref(id=7, name="Proveedor Hidraulica"),
        date_planned=datetime(2026, 9, 5, tzinfo=UTC),
        receipt_status="pending",
        amount_total=1200.5,
        currency_id=Ref(id=1, name="USD"),
    )
    return case.case_id


async def test_a_question_is_answered_from_the_case_facts(
    client: TestClient, module: MemoryDirectorModule, chat: ScriptedChatClient
) -> None:
    await _users(module)
    case_id = await _escalated_case(module)
    chat.responses.append(
        AssistantReply(reply="The order is 7 days late and the supplier has not answered.")
    )
    viewer = _token(client, "vic@x.com")
    code = (await module.cases.get(case_id)).code  # type: ignore[union-attr]

    turn = client.post(
        f"/api/cases/{code}/chat",
        json={"text": "What is going on with this order?"},
        headers=viewer,
    )
    assert turn.status_code == 200
    [asked, answered] = turn.json()["messages"]
    assert asked["role"] == "user" and asked["by"] == "vic@x.com"
    assert answered["role"] == "director" and answered["action"] is None
    prompt = chat.last_prompt_text()
    assert "P00066 with Proveedor Hidraulica: state purchase, planned delivery 2026-09-05" in prompt
    assert "#21 escalation: P00066 needs a person" in prompt and "po_late_escalate" in prompt
    assert "#19 send_email" in prompt and "subject: [P00066] Fecha de entrega" in prompt
    assert "Estimado proveedor: ¿dónde está la mercadería?" in prompt
    assert "not sent until someone approves it" in prompt
    assert "What is going on" in prompt
    history = client.get(f"/api/cases/{case_id}/chat", headers=viewer).json()
    assert [m["role"] for m in history] == ["user", "director"]


async def test_an_instruction_becomes_an_action_that_runs_only_when_confirmed(
    client: TestClient,
    module: MemoryDirectorModule,
    chat: ScriptedChatClient,
    supplier: FakeAgentCaller,
) -> None:
    await _users(module)
    case_id = await _escalated_case(module)
    chat.responses.append(
        AssistantReply(
            reply="I will ask Proveedor Hidraulica for a firm date and stress the delay.",
            action=ProposedAction(
                kind="request_eta",
                note="stress the 7 days of delay",
                explanation="Ask the supplier for a firm delivery date on P00066.",
            ),
        )
    )
    approver, viewer = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    turn = client.post(
        f"/api/cases/{case_id}/chat",
        json={"text": "Ask them for a firm date, they are 7 days late"},
        headers=approver,
    )
    proposed = turn.json()["messages"][1]
    assert proposed["action"]["kind"] == "request_eta" and proposed["action_status"] == "proposed"
    assert supplier.sent == []  # nothing ran yet

    assert (
        client.post(
            f"/api/cases/{case_id}/chat/{proposed['id']}/confirm", headers=viewer
        ).status_code
        == 403
    )
    supplier.replies.append(
        agent_reply(
            "request_eta",
            "chat_x",
            "sent",
            "Delivery date request sent to Proveedor Hidraulica",
            po_name="P00066",
        )
    )
    module.approvals.seed(
        77, kind="send_email", summary="Send reminder for P00066", po=(66, "P00066")
    )
    module.approvals.seed(78, kind="send_email", summary="other order", po=(65, "P00065"))
    done = client.post(f"/api/cases/{case_id}/chat/{proposed['id']}/confirm", headers=approver)
    assert done.status_code == 200
    assert (
        done.json()["messages"][0]["text"] == "Delivery date request sent to Proveedor Hidraulica"
    )
    [sent] = supplier.sent
    assert (
        '"kind":"request_eta"' in sent.task_json
        and "Ana asked: stress the 7 days of delay" in sent.task_json
        and '"require_approval":true' in sent.task_json
    )
    # the draft that was still waiting for this order is retired; other orders untouched
    assert [(r["id"], r["status"]) for r in module.approvals.resolved] == [
        (19, "rejected"),
        (77, "rejected"),
    ]
    assert "superseded" in module.approvals.resolved[0]["reason"]
    history = client.get(f"/api/cases/{case_id}/chat", headers=viewer).json()
    assert history[1]["action_status"] == "confirmed"
    kinds = [e.kind for e in await module.cases.events(case_id)]
    assert "task_sent" in kinds and "result" in kinds
    assert (
        client.post(
            f"/api/cases/{case_id}/chat/{proposed['id']}/confirm", headers=approver
        ).status_code
        == 409
    )


async def test_hold_and_close_actions_change_the_case(
    client: TestClient, module: MemoryDirectorModule, chat: ScriptedChatClient
) -> None:
    await _users(module)
    case_id = await _escalated_case(module)
    approver = _token(client, "ana@x.com")
    chat.responses.append(
        AssistantReply(
            reply="Holding.",
            action=ProposedAction(
                kind="hold_until",
                until="2026-09-20",
                note="wait for the container",
                explanation="Pause follow-ups until 2026-09-20.",
            ),
        )  # type: ignore[arg-type]
    )
    hold = client.post(
        f"/api/cases/{case_id}/chat", json={"text": "wait until the 20th"}, headers=approver
    ).json()["messages"][1]
    client.post(f"/api/cases/{case_id}/chat/{hold['id']}/confirm", headers=approver)
    case = await module.cases.get(case_id)
    assert (
        case is not None
        and case.next_action_at is not None
        and case.next_action_at.date().isoformat() == "2026-09-20"
    )

    chat.responses.append(
        AssistantReply(
            reply="Closing.",
            action=ProposedAction(
                kind="close_case",
                note="order cancelled in Odoo",
                explanation="Close the case and its escalation.",
            ),
        )
    )
    close = client.post(
        f"/api/cases/{case_id}/chat",
        json={"text": "I cancelled the order, close this"},
        headers=approver,
    ).json()["messages"][1]
    done = client.post(f"/api/cases/{case_id}/chat/{close['id']}/confirm", headers=approver).json()[
        "messages"
    ][0]
    assert "escalation resolved" in done["text"]
    assert (
        module.approvals.resolved[0]["id"] == 21
        and module.approvals.resolved[0]["reason"] == "order cancelled in Odoo"
    )
    case = await module.cases.get(case_id)
    assert case is not None and case.status == "done"


async def test_actions_that_need_an_order_are_dropped_and_dismiss_works(
    client: TestClient, module: MemoryDirectorModule, chat: ScriptedChatClient
) -> None:
    await _users(module)
    case, _ = await module.cases.attach_or_create(kind="planning", po_name=None)
    approver = _token(client, "ana@x.com")
    chat.responses.append(
        AssistantReply(
            reply="Sure.", action=ProposedAction(kind="follow_up", explanation="Send a reminder.")
        )
    )
    answer = client.post(
        f"/api/cases/{case.case_id}/chat", json={"text": "send a reminder"}, headers=approver
    ).json()["messages"][1]
    assert answer["action"] is None  # no order on a planning case

    chat.responses.append(
        AssistantReply(
            reply="Holding.",
            action=ProposedAction(kind="hold_until", until="2026-09-30", explanation="Pause."),
        )  # type: ignore[arg-type]
    )
    hold = client.post(
        f"/api/cases/{case.case_id}/chat", json={"text": "hold"}, headers=approver
    ).json()["messages"][1]
    dismissed = client.post(
        f"/api/cases/{case.case_id}/chat/{hold['id']}/dismiss", headers=approver
    ).json()["messages"][0]
    assert "dismissed by Ana" in dismissed["text"]
    history = client.get(f"/api/cases/{case.case_id}/chat", headers=approver).json()
    assert history[-2]["action_status"] == "dismissed"
    assert (
        client.post(
            f"/api/cases/{case.case_id}/chat/{hold['id']}/confirm", headers=approver
        ).status_code
        == 409
    )


async def test_an_unmatched_email_can_be_linked_to_an_order_from_the_chat(
    client: TestClient,
    module: MemoryDirectorModule,
    chat: ScriptedChatClient,
    supplier: FakeAgentCaller,
) -> None:
    await _users(module)
    case, _ = await module.cases.attach_or_create(kind="unlinked", po_name=None)
    await module.cases.add_event(
        case.case_id,
        "event_received",
        {
            "event_type": "inbound_mail.unlinked",
            "source": "mail_sync",
            "graph_message_id": "AAMk1",
            "sender_address": "ventas@x.com",
            "web_link": "https://outlook/x",
        },
    )
    approver = _token(client, "ana@x.com")
    chat.responses.append(
        AssistantReply(
            reply="I will link it to P00068 and read it.",
            action=ProposedAction(
                kind="link_email", po_name="p00068", explanation="Link the email to P00068."
            ),
        )
    )
    module.emails.messages["AAMk1"] = EmailSnapshot(
        subject="Cotización bombas",
        sender="ventas@x.com",
        text="Adjuntamos la cotización de las bombas para la orden P00068.",
        web_link="https://outlook/x",
    )
    turn = client.post(
        f"/api/cases/{case.case_id}/chat", json={"text": "this is for P00068"}, headers=approver
    )
    proposed = turn.json()["messages"][1]
    assert proposed["action"]["kind"] == "link_email"
    prompt = chat.last_prompt_text()
    assert 'from ventas@x.com on ?, subject "Cotización bombas"' in prompt
    assert "Adjuntamos la cotización de las bombas" in prompt
    assert module.emails.reads == ["AAMk1"]
    supplier.replies.append(
        agent_reply(
            "resolve_unlinked", "chat_x", "no_action", "email read: nothing new", po_name="P00068"
        )
    )
    done = client.post(f"/api/cases/{case.case_id}/chat/{proposed['id']}/confirm", headers=approver)
    assert done.status_code == 200 and done.json()["messages"][0]["text"].startswith(
        "Email linked to P00068"
    )
    [sent] = supplier.sent
    assert (
        '"assigned_po_name":"P00068"' in sent.task_json
        and '"graph_message_id":"AAMk1"' in sent.task_json
    )
    updated = await module.cases.get(case.case_id)
    assert updated is not None and updated.po_name == "P00068"
