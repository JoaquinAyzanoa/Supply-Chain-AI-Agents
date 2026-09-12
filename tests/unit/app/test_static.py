"""The built frontend is served next to the API; without a bundle nothing changes."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sc_core.app.static import mount_spa


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "yes"}

    return app


def test_without_a_bundle_the_app_is_untouched(tmp_path: Path) -> None:
    app = _app()
    assert mount_spa(app, tmp_path / "missing") is False
    with TestClient(app) as client:
        assert client.get("/api/ping").json() == {"pong": "yes"}
        assert client.get("/cases").status_code == 404


def test_bundle_is_served_with_spa_fallback(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    app = _app()
    assert mount_spa(app, dist) is True
    with TestClient(app) as client:
        assert client.get("/api/ping").json() == {"pong": "yes"}  # API routes still win
        assert client.get("/assets/index-abc.js").text == "console.log(1)"
        assert client.get("/favicon.svg").text == "<svg/>"
        for path in ("/", "/login", "/cases/case_1", "/planning/run_1"):
            response = client.get(path)
            assert response.status_code == 200 and 'id="root"' in response.text, path
        assert client.get("/api/nope").status_code == 404  # never index.html for the API
        assert client.get("/../pyproject.toml").status_code in (200, 404)
        assert 'id="root"' in client.get("/..%2Fpyproject.toml").text  # stays inside dist
