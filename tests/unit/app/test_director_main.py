"""The director service builds from the shared factory."""

from fastapi.testclient import TestClient
from loguru import logger


def test_director_app_serves_system_routes() -> None:
    from director.main import app, settings

    assert settings.service_name == "director"
    with TestClient(app) as c:
        live = c.get("/health/live").json()
        assert live["service"] == "director"
        assert c.get("/discovery").json()["service"] == "director"
    logger.remove()
