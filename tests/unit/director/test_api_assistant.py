"""The department assistant: answers cite records; a plan runs only once a person confirms."""

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
from director.desk.assistant import AssistantAnswer, Plan, PlanStep
from director.orchestration.policies import PoFacts
from director.testing import MemoryDirectorModule
from sc_core.a2a import AgentReply
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.llm.testing import ScriptedChatClient
from sc_core.odoo.models import PurchaseOrder, Ref
from sc_core.schema.a2a import Outcome, SourcingResult
from sc_core.shared.time import local_today

from .helpers import agent_reply

TODAY = local_today()


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def supplier() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def sourcing() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def module(
    chat: ScriptedChatClient, supplier: FakeAgentCaller, sourcing: FakeAgentCaller
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


def _sourcing_reply(kind: str, case_id: str, status: str, **extra: object) -> AgentReply:
    result = SourcingResult(
        kind=kind,  # type: ignore[arg-type]
        case_id=case_id,
        run_id="run_s1",
        outcome=Outcome(status=status, summary=f"{kind} {status}"),  # type: ignore[arg-type]
        **extra,  # type: ignore[arg-type]
    )
    return AgentReply(status="completed", text=result.model_dump_json())


async def _the_desk(module: MemoryDirectorModule) -> None:
    module.risk_source.report_data.update(
        products=[
            {
                "product_id": 1,
                "product_ref": "CBEA-LHN",
                "product_name": "Valvula de contrabalance",
                "position": 3,
                "p_stockout_30": 0.62,
                "p_stockout_60": 0.9,
                "open_po_names": ["P00077"],
                "late_po_names": ["P00077"],
                "suggested_qty": 44,
            }
        ],
        suppliers=[
            {
                "partner_id": 8,
                "partner_name": "Proveedor Hidraulica",
                "open_lines": 3,
                "overdue_lines": 2,
                "exposure": 3100.0,
            }
        ],
    )
    module.approvals.seed(
        24, kind="award", summary="Round #1: award to Proveedor Hidraulica", po=(81, "P00081")
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
    module.board_orders.add(
        PurchaseOrder(
            id=77,
            name="P00077",
            state="purchase",
            partner_id=Ref(id=8, name="Proveedor Hidraulica"),
            date_planned=datetime.combine(
                TODAY - timedelta(days=7), datetime.min.time(), tzinfo=UTC
            ),
            receipt_status="pending",
            amount_total=2314.44,
            currency_id=Ref(id=2, name="USD"),
        )
    )
    module.performance.rows.append(
        {
            "partner_id": 8,
            "partner_name": "Proveedor Hidraulica",
            "score": 75.5,
            "otif": 0.5,
            "lead_time_mean_days": 42.3,
            "scorecard": "Late on half of the deliveries this quarter.",
        }
    )


async def test_a_question_is_answered_from_the_desk_with_citations(
    client: TestClient, module: MemoryDirectorModule, chat: ScriptedChatClient
) -> None:
    await _users(module)
    await _the_desk(module)
    vic = _token(client, "vic@x.com")
    chat.responses.append(
        AssistantAnswer(
            reply="[P00077] is 7 days late; Hidraulica scores 75 and CBEA-LHN may run out [#24].",
            citations=["P00077", "Proveedor Hidraulica", "CBEA-LHN", "nope"],
        )
    )
    turn = client.post(
        "/api/assistant",
        json={"text": "Que pasa con P00077 y con Hidraulica? Y el stock de CBEA-LHN?"},
        headers=vic,
    )
    assert turn.status_code == 200
    asked, answered = turn.json()["messages"]
    assert asked["role"] == "user" and asked["by"] == "vic@x.com"
    assert answered["role"] == "director" and answered["plan"] is None
    # references the desk listed survive (listed or written in brackets), each with a link
    assert [c["ref"] for c in answered["citations"]] == [
        "P00077",
        "Proveedor Hidraulica",
        "CBEA-LHN",
        "#24",
    ]
    paths = {c["ref"]: c["path"] for c in answered["citations"]}
    assert paths["P00077"] == "/board?po=P00077" and paths["#24"] == "/approvals?id=24"
    assert paths["CBEA-LHN"] == "/risk" and paths["Proveedor Hidraulica"] == "/suppliers"
    # the model saw the order from Odoo, the scorecard and the risk line
    prompt = chat.last_prompt_text()
    assert "[P00077] with Proveedor Hidraulica: state purchase" in prompt
    assert "[Proveedor Hidraulica] score 75.5/100, OTIF 50%" in prompt
    assert "[CBEA-LHN] Valvula de contrabalance: position 3, 62% stockout odds" in prompt
    assert "[#24] award: Round #1" in prompt and "confirmed order 7 day(s) late" in prompt
    # the conversation is the person's own
    assert len(client.get("/api/assistant", headers=vic).json()) == 2
    assert client.get("/api/assistant", headers=_token(client, "ana@x.com")).json() == []


async def test_an_instruction_becomes_a_plan_that_runs_only_when_confirmed(
    client: TestClient,
    module: MemoryDirectorModule,
    chat: ScriptedChatClient,
    supplier: FakeAgentCaller,
    sourcing: FakeAgentCaller,
) -> None:
    await _users(module)
    await _the_desk(module)
    ana, vic = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    chat.responses.append(
        AssistantAnswer(
            reply="I will ask the market for 40 valves and press Hidraulica for a date.",
            citations=["CBEA-LHN", "P00077"],
            plan=Plan(
                summary="A quote round for the valve and a firm date on P00077",
                steps=[
                    PlanStep(
                        kind="quote_round",
                        product_ref="cbea-lhn",
                        qty=40,
                        deadline_days=5,
                        explanation="Ask the suppliers who list CBEA-LHN for 40 units.",
                    ),
                    PlanStep(
                        kind="quote_round",
                        product_ref="ZZ-9",
                        qty=1,
                        explanation="An unknown product: dropped.",
                    ),
                    PlanStep(
                        kind="request_eta",
                        po_name="p00077",
                        note="the order is a week late",
                        explanation="Ask Proveedor Hidraulica for a firm date on P00077.",
                    ),
                ],
            ),
        )
    )
    turn = client.post(
        "/api/assistant",
        json={"text": "Start a quote round for CBEA-LHN, 40 units, and ask for a date on P00077"},
        headers=ana,
    )
    proposed = turn.json()["messages"][1]
    plan = proposed["plan"]
    assert proposed["plan_status"] == "proposed"
    assert [s["kind"] for s in plan["steps"]] == ["quote_round", "request_eta"]
    assert plan["steps"][0]["product_ref"] == "CBEA-LHN" and plan["steps"][0]["product_id"] == 1
    assert plan["steps"][1]["po_name"] == "P00077"
    assert sourcing.sent == [] and supplier.sent == []  # nothing ran yet

    assert client.post(f"/api/assistant/{proposed['id']}/confirm", headers=vic).status_code == 403
    sourcing.replies.append(_sourcing_reply("quote_round", "plan_x", "sent", round_id=5))
    supplier.replies.append(
        agent_reply("request_eta", "chat_x", "awaiting_approval", "draft", approval_id=31)
    )
    done = client.post(f"/api/assistant/{proposed['id']}/confirm", headers=ana)
    assert done.status_code == 200
    body = done.json()
    assert body["plan_status"] == "confirmed"
    assert "quote_round CBEA-LHN: quote_round sent" in body["outcome"]
    assert "request_eta P00077:" in body["outcome"]
    round_task = sourcing.sent[0].task_json
    assert '"kind":"quote_round"' in round_task and '"product_id":1' in round_task
    assert '"qty":40.0' in round_task and '"deadline_days":5' in round_task
    eta_task = supplier.sent[0].task_json
    assert '"kind":"request_eta"' in eta_task and '"po_name":"P00077"' in eta_task
    assert '"require_approval":true' in eta_task  # a person reads the email first
    # a plan runs once
    assert client.post(f"/api/assistant/{proposed['id']}/confirm", headers=ana).status_code == 409
    assert client.post(f"/api/assistant/{proposed['id']}/dismiss", headers=ana).status_code == 409


async def test_a_plan_with_nothing_the_desk_can_run_is_dropped(
    client: TestClient, module: MemoryDirectorModule, chat: ScriptedChatClient
) -> None:
    await _users(module)
    await _the_desk(module)
    ana = _token(client, "ana@x.com")
    chat.responses.append(
        AssistantAnswer(
            reply="I cannot do that yet.",
            plan=Plan(
                summary="x",
                steps=[
                    PlanStep(
                        kind="start_playbook", po_name="P00077", playbook="nope", explanation="x"
                    ),
                    PlanStep(kind="hold_until", po_name="P00077", explanation="no date given"),
                ],
            ),
        )
    )
    turn = client.post("/api/assistant", json={"text": "do something odd"}, headers=ana)
    answered = turn.json()["messages"][1]
    assert answered["plan"] is None and answered["plan_status"] is None
    assert client.post(f"/api/assistant/{answered['id']}/confirm", headers=ana).status_code == 404
