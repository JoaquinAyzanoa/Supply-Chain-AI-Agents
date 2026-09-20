"""The autonomy API: policy, decide, preview, two-person widening, the feed and reverts."""

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
from director.autonomy import AutoAction
from director.testing import MemoryDirectorModule
from sc_core.app import create_application
from sc_core.infra.settings import Settings

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


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
    for email, role in (
        ("ana@x.com", "approver"),
        ("vic@x.com", "viewer"),
        ("adm@x.com", "admin"),
        ("bea@x.com", "admin"),
    ):
        await module.users.create(
            email=email,
            name=email.split("@")[0].title(),
            password_hash=hash_password("s3cret!!"),
            role=role,  # type: ignore[arg-type]
        )


def _token(client: TestClient, email: str) -> dict[str, str]:
    body = client.post("/api/auth/login", json={"email": email, "password": "s3cret!!"}).json()
    return {"Authorization": f"Bearer {body['token']}"}


def _rule(rule_id: str, **over: object) -> dict[str, object]:
    return {
        "id": rule_id,
        "kind": "send_email",
        "when": {"partner_ids": [8]},
        "level": "auto_notice",
        "revert_hours": 24,
        "note": "",
        "enabled": True,
        **over,
    }


async def test_policy_decide_and_preview(client: TestClient, module: MemoryDirectorModule) -> None:
    await _users(module)
    viewer = _token(client, "vic@x.com")
    policy = client.get("/api/autonomy", headers=viewer).json()
    assert policy["version"] == 0 and policy["policy"]["rules"] == [] and policy["pending"] is None

    draft = {"rules": [_rule("trusted")]}
    verdict = client.post(
        "/api/autonomy/decide",
        json={"kind": "send_email", "facts": {"partner_id": 8}, "policy": draft},
        headers=viewer,
    ).json()
    assert verdict["level"] == "auto_notice" and verdict["rule_id"] == "trusted"
    current = client.post(
        "/api/autonomy/decide",
        json={"kind": "send_email", "facts": {"partner_id": 8}},
        headers=viewer,
    ).json()
    assert current["level"] == "approve"  # the current policy has no rules

    # the last 30 days of approvals replayed against the draft
    module.approvals.seed(
        1,
        kind="send_email",
        summary="Send date request to Proveedor Hidraulica for P00074",
        payload={"facts": {"partner_id": 8, "email_kind": "request_eta"}},
        po=(74, "P00074"),
        status="approved",
    )
    module.approvals.seed(
        2,
        kind="send_email",
        summary="Send RFQ to Hidraulica Alterna",
        payload={"facts": {"partner_id": 9}},
    )
    module.approvals.seed(
        3, kind="vendor_bill", summary="Record invoice", payload={"facts": {"amount": 100}}
    )
    preview = client.post(
        "/api/autonomy/preview", json={"policy": draft, "days": 30}, headers=viewer
    ).json()
    assert preview["total"] == 3 and preview["would_run_alone"] == 1
    assert preview["by_rule"] == {"trusted": 1}
    assert preview["by_kind"]["send_email"] == {"auto_notice": 1, "approve": 1}
    assert [r["level"] for r in preview["rows"]] == ["auto_notice", "approve", "approve"]


async def test_lowering_saves_at_once_but_widening_needs_a_second_person(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    adm, bea, viewer = (
        _token(client, "adm@x.com"),
        _token(client, "bea@x.com"),
        _token(client, "vic@x.com"),
    )
    assert (
        client.put("/api/autonomy", json={"policy": {"rules": []}}, headers=viewer).status_code
        == 403
    )

    # a rule that keeps everything on approve only lowers: saved immediately as a new version
    saved = client.put(
        "/api/autonomy",
        json={"policy": {"rules": [_rule("careful", level="approve")]}, "note": "start"},
        headers=adm,
    ).json()
    assert saved["approval_id"] is None and saved["saved"]["version"] == 1
    assert [e.kind for e in module.realtime.published] == ["settings_changed"]

    # widening creates an autonomy_change approval instead
    widened = client.put(
        "/api/autonomy",
        json={"policy": {"rules": [_rule("trusted")]}, "note": "trust them"},
        headers=adm,
    ).json()
    assert widened["saved"] is None and widened["widened"] == ["trusted"]
    approval_id = widened["approval_id"]
    pending = client.get("/api/autonomy", headers=viewer).json()["pending"]
    assert pending == {
        "approval_id": approval_id,
        "requested_by": "adm@x.com",
        "widened": ["trusted"],
    }
    assert (
        client.get("/api/autonomy", headers=viewer).json()["policy"]["rules"][0]["id"] == "careful"
    )
    again = client.put("/api/autonomy", json={"policy": {"rules": [_rule("trusted")]}}, headers=adm)
    assert again.status_code == 409  # one change at a time

    # the requester cannot confirm their own widening; another person can
    own = client.post(
        f"/api/approvals/{approval_id}/resolve", json={"status": "approved"}, headers=adm
    )
    assert own.status_code == 403
    other = client.post(
        f"/api/approvals/{approval_id}/resolve", json={"status": "approved"}, headers=bea
    )
    assert other.status_code == 200
    applied = client.get("/api/autonomy", headers=viewer).json()
    assert applied["version"] == 2 and applied["policy"]["rules"][0]["id"] == "trusted"
    assert applied["pending"] is None
    assert module.runtime_settings.versions[-1].changed_by == "bea@x.com"


async def test_feed_lists_actions_and_reverts_inside_the_window(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    ana, viewer = _token(client, "ana@x.com"), _token(client, "vic@x.com")
    now = datetime.now(UTC)
    module.auto_actions.add(
        AutoAction(
            id=1,
            created_at=now - timedelta(hours=1),
            case_id="case_1",
            agent="supplier_comms",
            kind="po_change",
            level="auto_notice",
            rule_id="trusted-dates",
            summary="Moved 2 lines of P00074 by 3 days",
            po_id=74,
            po_name="P00074",
            partner_id=8,
            payload={"po_name": "P00074"},
            revert={
                "po_id": 74,
                "lines": [{"line_id": 1, "field": "date_planned", "before": "2026-09-25"}],
            },
            revert_until=now + timedelta(hours=23),
        )
    )
    module.auto_actions.add(
        AutoAction(
            id=2,
            created_at=now - timedelta(hours=2),
            agent="supplier_comms",
            kind="send_email",
            level="auto",
            rule_id="routine",
            summary="Sent a date request to Proveedor Hidraulica for P00077",
            po_name="P00077",
        )
    )
    module.auto_actions.add(
        AutoAction(
            id=3,
            created_at=now - timedelta(days=3),
            agent="supplier_comms",
            kind="po_change",
            level="auto_notice",
            summary="old",
            revert={"po_id": 1, "lines": []},
            revert_until=now - timedelta(days=2),
        )
    )
    feed = client.get("/api/autonomy/actions?days=7", headers=viewer).json()
    assert [a["id"] for a in feed] == [1, 2, 3]
    assert [a["revertible"] for a in feed] == [True, False, False]
    assert "revert" not in feed[0]

    assert client.post("/api/autonomy/actions/1/revert", headers=viewer).status_code == 403
    assert client.post("/api/autonomy/actions/2/revert", headers=ana).status_code == 422  # an email
    assert (
        client.post("/api/autonomy/actions/3/revert", headers=ana).status_code == 409
    )  # window closed
    assert client.post("/api/autonomy/actions/9/revert", headers=ana).status_code == 404
    done = client.post("/api/autonomy/actions/1/revert", headers=ana).json()
    assert done["id"] == 1 and "reverted by Ana" in done["note"]
    assert module.reverter.reverted == [(1, "Ana")]
    assert client.post("/api/autonomy/actions/1/revert", headers=ana).status_code == 409  # twice
    after = client.get("/api/autonomy/actions", headers=viewer).json()
    assert after[0]["reverted_by"] == "ana@x.com" and after[0]["revertible"] is False
