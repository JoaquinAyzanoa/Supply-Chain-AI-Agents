"""Test doubles for the Odoo client.

Three ways to test code that talks to Odoo, cheapest first:

1. ``ScriptedOdoo``: hand-written responses, one per expected request. Best
   for asserting *what* a repository asks (domains, payloads).
2. Cassettes (``RecordingTransport`` / ``ReplayTransport``): real responses
   captured once from the compose Odoo into ``tests/fixtures/odoo/*.json``
   and replayed offline. Best for agent tests that need realistic data
   without a server. Refresh with ``just odoo-record``.
3. The real container (``tests/integration``).

Cassette keys are built from the sanitised request (API key and uid
replaced by placeholders), so a replay never depends on which key or user
recorded it. Identical requests are answered in the recorded order, which
keeps read-create-read sequences faithful.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from sc_core.infra.settings import OdooCfg
from sc_core.odoo.client import OdooClient

# --- scripted responses ------------------------------------------------------


def rpc_ok(result: Any) -> httpx.Response:
    """A JSON-RPC success body. ``None`` mimics Odoo omitting the result key."""
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1}
    if result is not None:
        body["result"] = result
    return httpx.Response(200, json=body)


def rpc_error(name: str, message: str = "boom", debug: str = "Traceback...") -> httpx.Response:
    """A JSON-RPC error body shaped like Odoo's (``data.name`` is the exception class)."""
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
                raise AssertionError(f"unexpected Odoo request: {payload['params']}")
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


# --- cassettes -----------------------------------------------------------------

API_KEY_PLACEHOLDER = "<api_key>"
LOGIN_PLACEHOLDER = "<login>"
UID_PLACEHOLDER = "<uid>"


def sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Strip login, credentials and uid so keys are stable across users and environments."""
    service, method, args = params["service"], params["method"], list(params["args"])
    if service == "common" and method == "login" and len(args) == 3:
        args[1] = LOGIN_PLACEHOLDER
        args[2] = API_KEY_PLACEHOLDER
    elif service == "object" and method == "execute_kw" and len(args) >= 3:
        args[1] = UID_PLACEHOLDER
        args[2] = API_KEY_PLACEHOLDER
    return {"service": service, "method": method, "args": args}


def request_key(params: dict[str, Any]) -> str:
    canonical = json.dumps(sanitize_params(params), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


@dataclass
class Cassette:
    path: Path
    entries: list[dict[str, Any]] = field(default_factory=list)
    _cursor: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Cassette:
        if not path.exists():
            raise FileNotFoundError(
                f"cassette {path} not found; record it with `just odoo-record` (Odoo must be up)"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(path=path, entries=list(data["entries"]))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"entries": self.entries}, indent=1, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )

    def record(self, params: dict[str, Any], response: dict[str, Any]) -> None:
        self.entries.append(
            {"key": request_key(params), "request": sanitize_params(params), "response": response}
        )

    def replay(self, params: dict[str, Any]) -> dict[str, Any]:
        """Next recorded response for this request; the last one is reused when exhausted."""
        key = request_key(params)
        matches = [e for e in self.entries if e["key"] == key]
        if not matches:
            summary = sanitize_params(params)
            is_orm = summary["method"] == "execute_kw"
            target = summary["args"][3:5] if is_orm else summary["args"]
            raise AssertionError(
                f"no recorded response in {self.path.name} for {summary['method']} {target}; "
                "re-record with `just odoo-record`"
            )
        index = min(self._cursor.get(key, 0), len(matches) - 1)
        self._cursor[key] = index + 1
        return matches[index]["response"]


class RecordingTransport(httpx.AsyncBaseTransport):
    """Pass requests to a real transport and store every response in the cassette."""

    def __init__(self, cassette: Cassette, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._cassette = cassette
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        raw = await response.aread()
        params = json.loads(request.content)["params"]
        if response.status_code == 200:
            self._cassette.record(params, json.loads(raw))
        self._cassette.save()
        return httpx.Response(response.status_code, content=raw, headers=response.headers)

    async def aclose(self) -> None:
        await self._inner.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    def __init__(self, cassette: Cassette) -> None:
        self._cassette = cassette

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)["params"]
        return httpx.Response(200, json=self._cassette.replay(params))


def replay_client(path: Path, cfg: OdooCfg | None = None) -> OdooClient:
    """A client that answers from ``path`` and never touches the network."""
    return OdooClient(cfg or _offline_cfg(), transport=ReplayTransport(Cassette.load(path)))


def recording_client(path: Path, cfg: OdooCfg) -> OdooClient:
    """A real client whose responses are written to ``path`` (overwritten)."""
    cassette = Cassette(path=path)
    return OdooClient(cfg, transport=RecordingTransport(cassette))


def cassette_client(
    path: Path, *, record: bool, cfg_factory: Callable[[], OdooCfg] | None = None
) -> OdooClient:
    """Record when ``record`` is true (needs a live Odoo), replay otherwise."""
    if record:
        if cfg_factory is None:
            raise ValueError("cfg_factory is required to record")
        return recording_client(path, cfg_factory())
    return replay_client(path)


def _offline_cfg() -> OdooCfg:
    from pydantic import SecretStr

    return OdooCfg(url="http://odoo.replay", db="scai", login="replay", api_key=SecretStr("replay"))
