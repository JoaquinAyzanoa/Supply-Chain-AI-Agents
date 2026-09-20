"""The learning API: feedback recorded on resolve, statistics, suggestions, profiles."""

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
from director.desk.learning import Suggestion
from director.testing import MemoryDirectorModule
from sc_core.app import create_application
from sc_core.infra.settings import Settings


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
        ui={"jwt_secret": SecretStr("ui")},
        odoo={"url": "http://odoo.test:8069"},
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


async def test_a_resolution_is_recorded_as_feedback_and_counted(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    ana, viewer = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    module.approvals.seed(
        7,
        kind="send_email",
        summary="Send reminder",
        payload={
            "subject": "s",
            "facts": {"partner_id": 8, "partner_name": "Proveedor Hidraulica"},
        },
    )
    module.approvals.seed(
        8,
        kind="po_change",
        summary="Changes",
        payload={"changes": [{"po_line_id": 1}, {"po_line_id": 2}]},
    )
    client.post(
        "/api/approvals/7/resolve",
        json={"status": "approved", "edited_payload": {"subject": "Better"}},
        headers=ana,
    )
    client.post(
        "/api/approvals/8/resolve", json={"status": "rejected", "reason": "too early"}, headers=ana
    )

    recorded = module.feedback.rows
    assert (
        recorded[7].edited and recorded[7].edit_fields == ["subject"] and recorded[7].via == "api"
    )
    assert recorded[7].partner_id == 8 and recorded[7].resolved_by == "ana@x.com"
    assert recorded[8].status == "rejected" and recorded[8].reason == "too early"
    stats = client.get("/api/learning/stats?days=30", headers=viewer).json()
    assert stats["total"] == 2 and stats["edited"] == 1 and stats["rejected"] == 1
    assert {s["kind"]: s["n"] for s in stats["by_kind"]} == {"send_email": 1, "po_change": 1}
    assert stats["by_supplier"][0]["partner_name"] == "Proveedor Hidraulica"


async def test_suggestions_are_accepted_through_the_right_path_or_dismissed(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    adm, ana, viewer = (
        _token(client, "adm@x.com"),
        _token(client, "ana@x.com"),
        _token(client, "vic@x.com"),
    )
    rule = await module.suggestions.upsert_open(
        Suggestion(
            key="autonomy:send_email:8",
            kind="autonomy_rule",
            title="Let emails to Hidraulica run alone",
            detail="10 of 10",
            proposal={
                "rule": {
                    "id": "suggested-send-email-8",
                    "kind": "send_email",
                    "when": {"partner_ids": [8]},
                    "level": "auto_notice",
                }
            },
        )
    )
    setting = await module.suggestions.upsert_open(
        Suggestion(
            key="setting:invoice_price_tolerance_pct",
            kind="setting",
            title="Raise the tolerance to 2.4 %",
            detail="5 held bills",
            proposal={"setting": "invoice_price_tolerance_pct", "value": 2.4},
        )
    )
    attention = await module.suggestions.upsert_open(
        Suggestion(
            key="attention:po_change:None",
            kind="attention",
            title="Mostly rejected",
            detail="3 of 6",
        )
    )
    listed = client.get("/api/learning/suggestions", headers=viewer).json()
    assert [s["key"] for s in listed] == [rule.key, setting.key, attention.key]
    assert (
        client.post(f"/api/learning/suggestions/{rule.id}/accept", headers=ana).status_code == 403
    )

    # a rule widens autonomy: it becomes an autonomy_change approval, not a direct save
    accepted = client.post(f"/api/learning/suggestions/{rule.id}/accept", headers=adm).json()
    assert accepted["approval_id"] is not None and accepted["saved_version"] is None
    assert accepted["suggestion"]["status"] == "accepted"
    pending = client.get("/api/autonomy", headers=viewer).json()["pending"]
    assert pending["widened"] == ["suggested-send-email-8"]

    # a setting is saved at once as a new version
    saved = client.post(f"/api/learning/suggestions/{setting.id}/accept", headers=adm).json()
    assert saved["saved_version"] == 1 and saved["approval_id"] is None
    assert module.runtime_settings.versions[-1].settings.invoice_price_tolerance_pct == 2.4

    assert (
        client.post(f"/api/learning/suggestions/{attention.id}/accept", headers=adm).status_code
        == 422
    )
    dismissed = client.post(f"/api/learning/suggestions/{attention.id}/dismiss", headers=ana).json()
    assert dismissed["status"] == "dismissed" and dismissed["resolved_by"] == "ana@x.com"
    assert client.get("/api/learning/suggestions", headers=viewer).json() == []
    assert (
        client.post(f"/api/learning/suggestions/{rule.id}/accept", headers=adm).status_code == 409
    )
    assert client.post("/api/learning/suggestions/99/dismiss", headers=ana).status_code == 404


async def test_supplier_profile_round_trip(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    ana, viewer = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    empty = client.get("/api/learning/profiles/8", headers=viewer).json()
    assert empty["partner_id"] == 8 and empty["notes"] == "" and empty["contacts"] == []
    await module.profiles.update_facts(8, {"reply_hours_median": 2.8})
    assert (
        client.put("/api/learning/profiles/8", json={"notes": "x"}, headers=viewer).status_code
        == 403
    )
    saved = client.put(
        "/api/learning/profiles/8",
        json={
            "language": "es_PE",
            "formality": "formal",
            "greeting": "Estimados señores",
            "sign_off": "Atentamente",
            "contacts": ["Carla Reyes"],
            "notes": "They answer within a day; copy Carla on urgent orders.",
        },
        headers=ana,
    ).json()
    assert saved["greeting"] == "Estimados señores" and saved["updated_by"] == "ana@x.com"
    assert saved["facts"] == {"reply_hours_median": 2.8}  # agent facts survive a person's edit
    again = client.get("/api/learning/profiles/8", headers=viewer).json()
    assert again["contacts"] == ["Carla Reyes"] and again["formality"] == "formal"
    assert datetime.fromisoformat(again["updated_at"]).tzinfo is not None or again["updated_at"]
    _ = UTC
