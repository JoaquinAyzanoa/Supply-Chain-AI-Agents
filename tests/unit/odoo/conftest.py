"""Helpers to test the Odoo client without a server: scripted httpx transport."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from sc_core.infra.settings import OdooCfg
from sc_core.odoo.client import OdooClient

Responder = Callable[[dict[str, Any]], httpx.Response | Exception]


def rpc_ok(result: Any) -> httpx.Response:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1}
    if result is not None:
        body["result"] = result
    return httpx.Response(200, json=body)


def rpc_error(name: str, message: str = "boom", debug: str = "Traceback...") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": 200,
                "message": "Odoo Server Error",
                "data": {"name": name, "message": message, "debug": debug, "arguments": [message]},
            },
        },
    )


@dataclass
class ScriptedOdoo:
    """Answers each request from a queue and records what was asked."""

    script: list[httpx.Response | Exception] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.requests.append(payload)
            if not self.script:
                raise AssertionError(f"unexpected request: {payload['params']}")
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        return httpx.MockTransport(handler)

    def calls(self) -> list[tuple[str, str]]:
        return [(r["params"]["service"], r["params"]["method"]) for r in self.requests]

    def execute_kw_args(self, index: int = -1) -> tuple[str, str, list[Any], dict[str, Any]]:
        """(model, method, args, kwargs) of the n-th execute_kw request."""
        req = [r for r in self.requests if r["params"]["method"] == "execute_kw"][index]
        _db, _uid, _key, model, method, args, kwargs = req["params"]["args"]
        return model, method, args, kwargs


@dataclass
class Sleeps:
    delays: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def make_cfg(**overrides: Any) -> OdooCfg:
    values: dict[str, Any] = {
        "url": "http://odoo.test:8069",
        "db": "scai",
        "login": "sc_agent_bot",
        "api_key": SecretStr("k3y"),
        "max_retries": 2,
    }
    values.update(overrides)
    return OdooCfg(**values)


@pytest.fixture
def odoo() -> ScriptedOdoo:
    return ScriptedOdoo()


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def client_factory(odoo: ScriptedOdoo, sleeps: Sleeps) -> Iterator[Callable[..., OdooClient]]:
    clients: list[OdooClient] = []

    def make(**overrides: Any) -> OdooClient:
        c = OdooClient(make_cfg(**overrides), transport=odoo.transport(), sleep=sleeps)
        clients.append(c)
        return c

    yield make


LOGIN_OK = rpc_ok(8)
