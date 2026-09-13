"""The morning briefing: sections from facts only, one paragraph from the model, an email."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
from director.autonomy import AutoAction
from director.briefing import Paragraph, facts_text
from director.policies import PoFacts
from director.testing import MemoryDirectorModule
from sc_core.app import create_application
from sc_core.infra.settings import Settings
from sc_core.llm.testing import ScriptedChatClient
from sc_core.shared.errors import ScError
from sc_core.shared.time import local_today

TODAY = local_today()


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def module(chat: ScriptedChatClient) -> MemoryDirectorModule:
    return MemoryDirectorModule(chat=chat)


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


async def _a_busy_desk(module: MemoryDirectorModule) -> None:
    """Two approvals, a product at risk, a late order, a closed case, an automatic reminder
    and one active playbook: the facts every section is built from."""
    module.approvals.seed(
        24,
        kind="internal_request",
        summary="Internal request from ana@empresa.com: 2 item(s)",
        payload={"facts": {"amount": 1050.0, "currency": "USD"}},
        po=None,
        thread_id="case_int1",
    )
    module.approvals.seed(
        17,
        kind="award",
        summary="Round #1 on P00081: award recommended to Proveedor Hidraulica",
        payload={"facts": {"amount": 4200.0, "currency": "USD"}},
        po=(81, "P00081"),
        thread_id="round_1",
    )
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
            },
            {
                "product_id": 2,
                "product_ref": "RPEC-LAN",
                "product_name": "Alivio",
                "position": 40,
                "p_stockout_30": 0.05,
                "p_stockout_60": 0.1,
                "open_po_names": [],
                "late_po_names": [],
                "suggested_qty": 0,
            },
        ],
        suppliers=[
            {
                "partner_id": 8,
                "partner_name": "Proveedor Hidraulica",
                "open_lines": 3,
                "overdue_lines": 2,
                "expected_late_lines": 2.4,
                "exposure": 3100.0,
            }
        ],
        at_risk_30=1,
        cash_exposure=3100.0,
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
    case, _ = await module.cases.attach_or_create(kind="inbound", po_name="P00074", partner_id=8)
    await module.cases.update(case.case_id, status="done", summary="dates applied on P00074")
    module.auto_actions.add(
        AutoAction(
            id=1,
            created_at=datetime.now(UTC) - timedelta(hours=3),
            agent="supplier_comms",
            kind="send_email",
            level="auto_notice",
            rule_id="reminders",
            summary="Reminder sent to Proveedor Hidraulica for P00080",
            po_name="P00080",
            revert={"x": 1},
            revert_until=datetime.now(UTC) + timedelta(hours=20),
        )
    )
    await module.playbooks.start("late_order", po_name="P00077", partner_id=8, advance=False)


async def test_the_briefing_is_built_from_facts_and_the_model_adds_one_paragraph(
    client: TestClient, module: MemoryDirectorModule, chat: ScriptedChatClient
) -> None:
    await _users(module)
    await _a_busy_desk(module)
    ana, vic = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    assert client.get("/api/briefing", headers=vic).status_code == 404
    assert client.post("/api/briefing/run", headers=vic).status_code == 403

    chat.responses.append(
        Paragraph(text="Two decisions wait for you; the valve CBEA-LHN needs a source today.")
    )
    built = client.post("/api/briefing/run", headers=ana)
    assert built.status_code == 201
    briefing = built.json()
    assert briefing["day"] == TODAY.isoformat()
    assert briefing["paragraph"].startswith("Two decisions wait for you")
    sections = {s["key"]: s for s in briefing["sections"]}
    assert [s["key"] for s in briefing["sections"]] == [
        "needs_you",
        "risks",
        "late",
        "overnight",
        "ran_alone",
        "playbooks",
    ]
    # the costly decision first, each with a link into the inbox
    needs = sections["needs_you"]
    assert needs["count"] == 2
    assert [i["path"] for i in needs["items"]] == ["/approvals?id=17", "/approvals?id=24"]
    assert needs["items"][0]["text"].startswith("#17 award: Round #1")
    assert "4,200 USD" in needs["items"][0]["text"]
    # only the product above the 20% bar, with its late order; the supplier's overdue lines
    risks = sections["risks"]
    assert risks["count"] == 1
    assert risks["items"][0]["text"] == (
        "CBEA-LHN: 62% chance of a stockout within 30 days · late order P00077"
    )
    assert risks["items"][0]["probability"] == 0.62 and risks["items"][0]["path"] == "/risk"
    assert risks["items"][1]["text"] == "Proveedor Hidraulica: 2 overdue line(s)"
    late = sections["late"]
    assert late["count"] == 1 and late["items"][0]["days"] == 7
    assert late["items"][0]["text"] == "P00077: 7 day(s) past its date without a receipt"
    overnight = sections["overnight"]
    assert overnight["count"] >= 1
    assert any(
        "dates applied on P00074" in i["text"] and i["path"].startswith("/cases/")
        for i in overnight["items"]
    )
    ran = sections["ran_alone"]
    assert ran["count"] == 1
    assert ran["items"][0]["text"] == (
        "Reminder sent to Proveedor Hidraulica for P00080 · rule reminders · still revertible"
    )
    plans = sections["playbooks"]
    assert plans["count"] == 1 and plans["items"][0]["text"] == "Late order: 1 active (1 × ask_eta)"
    # the model saw the facts only, as text
    prompt = chat.last_prompt_text()
    assert "Needs a decision today (2)" in prompt and "#17 award" in prompt
    assert briefing["counts"] == {
        "needs_you": 2,
        "risks": 1,
        "late": 1,
        "overnight": overnight["count"],
        "ran_alone": 1,
        "playbooks": 1,
    }
    assert client.get("/api/briefing", headers=vic).json()["day"] == TODAY.isoformat()
    assert len(client.get("/api/briefing/history", headers=vic).json()) == 1
    assert client.get(f"/api/briefing?day={date(2020, 1, 1)}", headers=vic).status_code == 404


async def test_without_the_model_the_sections_still_stand(
    client: TestClient, module: MemoryDirectorModule, chat: ScriptedChatClient
) -> None:
    await _users(module)
    await _a_busy_desk(module)
    ana = _token(client, "ana@x.com")
    chat.responses.append(ScError("provider down"))
    built = client.post("/api/briefing/run", headers=ana)
    assert built.status_code == 201
    assert built.json()["paragraph"] is None and built.json()["counts"]["needs_you"] == 2


async def test_the_briefing_is_emailed_with_links_back_and_never_a_body(
    client: TestClient, module: MemoryDirectorModule, chat: ScriptedChatClient
) -> None:
    await _users(module)
    await _a_busy_desk(module)
    ana = _token(client, "ana@x.com")
    assert (
        client.post("/api/briefing/email", json={"to": ["b@x.com"]}, headers=ana).status_code == 404
    )
    chat.responses.append(Paragraph(text="All under control."))
    client.post("/api/briefing/run", headers=ana)
    # nobody set recipients yet: the caller must name them
    assert client.post("/api/briefing/email", json={}, headers=ana).status_code == 422
    sent = client.post("/api/briefing/email", json={"to": ["ana@x.com", "b@x.com"]}, headers=ana)
    assert sent.status_code == 200 and sent.json()["sent_to"] == ["ana@x.com", "b@x.com"]
    [message] = module.sent_mail
    assert message.to == ["ana@x.com", "b@x.com"]
    assert message.subject == f"Purchasing briefing for {TODAY.isoformat()}"
    assert "All under control." in message.html_body
    assert 'href="https://tower.test/approvals?id=17"' in message.html_body
    assert 'href="https://tower.test/briefing"' in message.html_body
    assert "<p>secret" not in message.html_body
    assert client.get("/api/briefing", headers=ana).json()["emailed_to"] == ["ana@x.com", "b@x.com"]


def test_facts_text_lists_every_section_with_its_count() -> None:
    from director.briefing import Briefing, BriefingItem, BriefingSection

    briefing = Briefing(
        day=TODAY,
        since=datetime.now(UTC),
        sections=[
            BriefingSection(
                key="needs_you",
                title="Needs a decision today",
                count=1,
                items=[BriefingItem(text="#1 x")],
            ),
            BriefingSection(key="risks", title="Risks", count=0),
        ],
    )
    assert facts_text(briefing) == "Needs a decision today (1):\n- #1 x\nRisks (0):\n- (nothing)"
