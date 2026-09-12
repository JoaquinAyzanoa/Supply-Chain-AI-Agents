"""The approvals inbox API: listing with context and the single resolve path."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import SecretStr

from director import __version__
from director.api import api_router
from director.api.auth import hash_password
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


async def _seed_case(module: MemoryDirectorModule) -> str:
    case, _ = await module.cases.attach_or_create(kind="rfq", po_name="P00015")
    await module.cases.update(case.case_id, trace_id="tr1")
    await module.cases.add_event(
        case.case_id, "task_sent", {"task": "follow_up", "thread_id": "case_msg1"}
    )
    await module.cases.add_event(
        case.case_id,
        "rule_fired",
        {"rule": "rfq_silent", "reason": "no reply to the RFQ for 3 days"},
    )
    return case.case_id


async def test_inbox_lists_pending_with_payload_why_and_links(
    client: TestClient, module: MemoryDirectorModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sc_core.infra import tracing

    monkeypatch.setattr(tracing, "_host", "http://langfuse.test")
    await _users(module)
    case_id = await _seed_case(module)
    module.approvals.seed(
        7,
        kind="send_email",
        summary="Send reminder to Proveedor for P00015",
        payload={
            "subject": "[P00015] Follow-up",
            "html_body": "<p>Hi</p>",
            "web_link": "https://outlook/x",
        },
    )
    module.approvals.seed(
        8, kind="po_change", summary="Changes on P00016", po=("16", "P00016"), thread_id="other"
    )
    module.approvals.seed(9, kind="send_email", summary="old", status="approved")

    rows = client.get("/api/approvals", headers=_token(client, "vic@x.com")).json()
    assert [r["id"] for r in rows] == [7, 8]
    first = rows[0]
    assert first["payload"]["subject"] == "[P00015] Follow-up" and first["po_name"] == "P00015"
    assert (
        first["why"] == "rfq_silent: no reply to the RFQ for 3 days" and first["case_id"] == case_id
    )
    assert first["links"] == {
        "odoo": "http://odoo.test:8069/odoo/sc.approval/7",
        "order": "http://odoo.test:8069/odoo/purchase.order/15",
        "outlook": "https://outlook/x",
        "trace": "http://langfuse.test/trace/tr1",
    }
    assert rows[1]["why"] is None and rows[1]["links"]["outlook"] is None
    only = client.get("/api/approvals?kind=po_change", headers=_token(client, "vic@x.com")).json()
    assert [r["id"] for r in only] == [8]
    everything = client.get(
        "/api/approvals?status=all&po=P00015", headers=_token(client, "vic@x.com")
    ).json()
    assert [r["id"] for r in everything] == [7, 9]
    assert client.get("/api/approvals/7", headers=_token(client, "vic@x.com")).json()["id"] == 7
    assert client.get("/api/approvals/99", headers=_token(client, "vic@x.com")).status_code == 404
    assert client.get("/api/approvals").status_code == 401


async def test_resolve_validates_edits_writes_odoo_once_and_records_the_case(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    case_id = await _seed_case(module)
    module.approvals.seed(7, kind="send_email", summary="Send reminder", payload={"subject": "s"})
    approver = _token(client, "ana@x.com")

    bad = client.post(
        "/api/approvals/7/resolve",
        json={"status": "approved", "edited_payload": {"accepted_line_ids": [1]}},
        headers=approver,
    )
    assert bad.status_code == 422  # not an EmailEdits payload

    ok = client.post(
        "/api/approvals/7/resolve",
        json={"status": "approved", "edited_payload": {"subject": "Better subject"}},
        headers=approver,
    )
    assert ok.status_code == 200
    assert ok.json() == {
        "id": 7,
        "status": "approved",
        "resolved_by": "Ana",
        "callback_status": "sent",
    }
    [call] = module.approvals.resolved
    assert call["by_name"] == "Ana" and call["details"] == {"subject": "Better subject"}
    events = await module.cases.events(case_id)
    assert events[-1].kind == "approval_resolved"
    assert events[-1].payload["by"] == "ana@x.com" and events[-1].payload["via"] == "api"

    again = client.post("/api/approvals/7/resolve", json={"status": "approved"}, headers=approver)
    assert again.status_code == 409 and len(module.approvals.resolved) == 1


async def test_reject_with_reason_and_role_enforcement(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    module.approvals.seed(7, kind="po_change", summary="Changes", thread_id=None)
    viewer = _token(client, "vic@x.com")
    assert (
        client.post(
            "/api/approvals/7/resolve", json={"status": "rejected"}, headers=viewer
        ).status_code
        == 403
    )
    approver = _token(client, "ana@x.com")
    rejected = client.post(
        "/api/approvals/7/resolve",
        json={
            "status": "rejected",
            "reason": "wait for the month end",
            "edited_payload": {"accepted_line_ids": [1]},
        },
        headers=approver,
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    [call] = module.approvals.resolved
    assert (
        call["reason"] == "wait for the month end" and call["details"] is None
    )  # edits ignored on reject


async def test_plan_edits_are_validated_and_forwarded(
    client: TestClient, module: MemoryDirectorModule
) -> None:
    await _users(module)
    module.approvals.seed(12, kind="planning_run", summary="Plan", po=None, thread_id="plan_1")
    approver = _token(client, "ana@x.com")
    ok = client.post(
        "/api/approvals/12/resolve",
        json={
            "status": "approved",
            "edited_payload": {
                "accepted_line_ids": ["run:1", "run:4"],
                "edits": {"run:1": {"order_qty": 30}},
            },
        },
        headers=approver,
    )
    assert ok.status_code == 200
    assert module.approvals.resolved[0]["details"] == {
        "accepted_line_ids": ["run:1", "run:4"],
        "edits": {"run:1": {"order_qty": 30.0}},
    }
    module.approvals.seed(13, kind="escalation", summary="Esc", po=None, thread_id="c")
    assert (
        client.post(
            "/api/approvals/13/resolve",
            json={"status": "approved", "edited_payload": {"x": 1}},
            headers=approver,
        ).status_code
        == 422
    )
