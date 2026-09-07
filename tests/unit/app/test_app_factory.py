"""Tests for sc_core.app.create_application and its middlewares."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import Injected
from injector import Binder, Module, singleton
from loguru import logger

from sc_core.app import create_application
from sc_core.infra import context
from sc_core.infra.settings import Settings
from sc_core.shared.errors import ExternalServiceError, NotFound


class Greeter:
    def hello(self) -> str:
        return "hola"


class GreeterModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(Greeter, to=Greeter(), scope=singleton)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"service_name": "test-svc", "environment": "test"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _router() -> APIRouter:
    r = APIRouter()

    @r.get("/echo-context")
    async def echo_context() -> dict[str, Any]:
        return context.snapshot()

    @r.post("/upload")
    async def upload(payload: dict[str, Any]) -> dict[str, int]:
        return {"keys": len(payload)}

    @r.get("/missing")
    async def missing() -> None:
        raise NotFound("PO00123 not found", details={"model": "purchase.order"})

    @r.get("/upstream")
    async def upstream() -> None:
        raise ExternalServiceError("odoo timeout", service="odoo")

    @r.get("/crash")
    async def crash() -> None:
        raise RuntimeError("secret internal detail")

    @r.get("/greet")
    async def greet(greeter: Greeter = Injected(Greeter)) -> dict[str, str]:
        return {"msg": greeter.hello()}

    @r.get("/settings-name")
    async def settings_name(settings: Settings = Injected(Settings)) -> dict[str, str]:
        return {"name": settings.service_name}

    return r


@pytest.fixture
def app() -> FastAPI:
    async def ok() -> None:
        return None

    return create_application(
        _settings(),
        version="9.9.9",
        routers=[_router()],
        health_checks=[("always_ok", ok, True)],
        modules=[GreeterModule()],
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    logger.remove()
    logger.configure(patcher=None)


# --- system routes -----------------------------------------------------------


def test_live(client: TestClient) -> None:
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["service"] == "test-svc"
    assert r.json()["status"] == "ok"


def test_ready_ok(client: TestClient) -> None:
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"][0]["name"] == "always_ok"


def test_ready_503_on_critical_failure() -> None:
    async def down() -> None:
        raise ConnectionError("no route")

    app = create_application(_settings(), health_checks=[("db", down, True)])
    with TestClient(app) as c:
        r = c.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "fail"
    assert r.json()["checks"][0]["detail"] == "ConnectionError: no route"


def test_discovery_lists_routes_and_versions(client: TestClient) -> None:
    r = client.get("/discovery")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "test-svc"
    assert body["version"] == "9.9.9"
    assert body["environment"] == "test"
    paths = {route["path"] for route in body["routes"]}
    assert {"/health/live", "/health/ready", "/discovery", "/upload"} <= paths
    assert "/openapi.json" not in paths
    upload = next(route for route in body["routes"] if route["path"] == "/upload")
    assert upload["methods"] == ["POST"]


def test_docs_disabled_outside_dev(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404


# --- middlewares -------------------------------------------------------------


def test_request_id_is_generated_and_bound(client: TestClient) -> None:
    r = client.get("/echo-context")
    rid = r.headers["X-Request-ID"]
    assert len(rid) == 32
    assert r.json()["request_id"] == rid
    assert r.json()["service_name"] == "test-svc"


def test_request_id_is_echoed_when_valid(client: TestClient) -> None:
    r = client.get("/echo-context", headers={"X-Request-ID": "trace-abc.1"})
    assert r.headers["X-Request-ID"] == "trace-abc.1"
    assert r.json()["request_id"] == "trace-abc.1"


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    r = client.get("/echo-context", headers={"X-Request-ID": "bad id with spaces"})
    assert r.headers["X-Request-ID"] != "bad id with spaces"


def test_security_headers_present(client: TestClient) -> None:
    r = client.get("/health/live")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in r.headers


def test_hsts_when_enabled() -> None:
    app = create_application(_settings(http={"enable_hsts": True}))
    with TestClient(app) as c:
        assert "max-age=" in c.get("/health/live").headers["Strict-Transport-Security"]


def test_413_on_declared_content_length() -> None:
    app = create_application(_settings(http={"max_request_bytes": 100}), routers=[_router()])
    with TestClient(app) as c:
        r = c.post(
            "/upload",
            content=b'{"k":"' + b"x" * 200 + b'"}',
            headers={"Content-Type": "application/json"},
        )
    assert r.status_code == 413
    assert r.json()["code"] == "payload_too_large"
    assert r.json()["details"]["max_bytes"] == 100


def test_413_on_streamed_body_without_length() -> None:
    app = create_application(_settings(http={"max_request_bytes": 100}), routers=[_router()])

    def chunks() -> Iterator[bytes]:
        yield b'{"k":"'
        yield b"x" * 200
        yield b'"}'

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/upload", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["code"] == "payload_too_large"


def test_body_under_limit_passes() -> None:
    app = create_application(_settings(http={"max_request_bytes": 100}), routers=[_router()])
    with TestClient(app) as c:
        r = c.post("/upload", json={"a": 1, "b": 2})
    assert r.status_code == 200
    assert r.json() == {"keys": 2}


# --- error handling ----------------------------------------------------------


def test_domain_error_maps_to_status_and_body(client: TestClient) -> None:
    r = client.get("/missing")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"
    assert body["message"] == "PO00123 not found"
    assert body["details"] == {"model": "purchase.order"}
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_external_error_is_502_and_retryable(client: TestClient) -> None:
    r = client.get("/upstream")
    assert r.status_code == 502
    assert r.json()["retryable"] is True
    assert r.json()["details"]["service"] == "odoo"


def test_unexpected_error_is_500_without_details(client: TestClient) -> None:
    r = client.get("/crash")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error"
    assert "secret internal detail" not in r.text
    assert body["request_id"] == r.headers["X-Request-ID"]


# --- dependency injection ----------------------------------------------------


def test_injected_settings_and_custom_module(client: TestClient) -> None:
    assert client.get("/settings-name").json() == {"name": "test-svc"}
    assert client.get("/greet").json() == {"msg": "hola"}


# --- lifespan ----------------------------------------------------------------


def test_startup_and_shutdown_hooks_run_in_order() -> None:
    calls: list[str] = []

    async def s1() -> None:
        calls.append("s1")

    async def s2() -> None:
        calls.append("s2")

    async def d1() -> None:
        calls.append("d1")

    async def d2() -> None:
        calls.append("d2")

    app = create_application(_settings(), startup=[s1, s2], shutdown=[d1, d2])
    with TestClient(app):
        assert calls == ["s1", "s2"]
    assert calls == ["s1", "s2", "d2", "d1"]
