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
