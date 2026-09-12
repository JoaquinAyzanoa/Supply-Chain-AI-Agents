"""The inventory_planning service builds from the shared factory and serves the system routes."""

from fastapi.testclient import TestClient
from loguru import logger


def test_inventory_planning_app_serves_system_routes() -> None:
    from inventory_planning.main import app, settings

    assert settings.service_name == "inventory_planning"
    with TestClient(app) as c:
        assert c.get("/health/live").json()["service"] == "inventory_planning"
        paths = {r["path"] for r in c.get("/discovery").json()["routes"]}
        assert "/approvals/callback" in paths
    logger.remove()


def test_callback_accepts_nested_and_flat_details() -> None:
    from inventory_planning.routers.approvals import ApprovalCallback

    nested = ApprovalCallback.model_validate(
        {
            "approval_id": 3,
            "status": "approved",
            "thread_id": "plan_1",
            "resolved_by": "sc_agent_bot",
            "resolved_by_name": "Ana",
            "details": {"accepted_line_ids": ["r:1"], "edits": {"r:1": {"order_qty": 30}}},
        }
    ).decision()
    assert nested["resolved_by"] == "Ana"
    assert nested["details"] == {"accepted_line_ids": ["r:1"], "edits": {"r:1": {"order_qty": 30}}}
    flat = ApprovalCallback.model_validate(
        {"approval_id": 3, "status": "approved", "thread_id": "plan_1", "accepted_line_ids": []}
    ).decision()
    assert flat["details"] == {"accepted_line_ids": []} and flat["resolved_by"] is None
