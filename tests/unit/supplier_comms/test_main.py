"""The supplier_comms service builds from the shared factory and serves the system routes."""

from fastapi.testclient import TestClient
from loguru import logger


def test_supplier_comms_app_serves_system_routes() -> None:
    from supplier_comms.main import app, settings

    assert settings.service_name == "supplier_comms"
    with TestClient(app) as c:
        assert c.get("/health/live").json()["service"] == "supplier_comms"
        paths = {r["path"] for r in c.get("/discovery").json()["routes"]}
        assert "/approvals/callback" in paths
    logger.remove()
