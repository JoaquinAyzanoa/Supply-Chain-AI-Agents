"""Fixtures for Odoo unit tests: scripted transport and cassette replay."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from sc_core.infra.settings import OdooCfg, Settings, reset_settings_cache
from sc_core.odoo.client import OdooClient
from sc_core.odoo.testing import ScriptedOdoo, cassette_client, rpc_error, rpc_ok

__all__ = ["LOGIN_OK", "ScriptedOdoo", "Sleeps", "make_cfg", "rpc_error", "rpc_ok"]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "odoo"
RECORD = os.environ.get("SC_ODOO_RECORD") == "1"


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
    def make(**overrides: Any) -> OdooClient:
        return OdooClient(make_cfg(**overrides), transport=odoo.transport(), sleep=sleeps)

    yield make


def _live_cfg() -> OdooCfg:
    reset_settings_cache()
    cfg = Settings().odoo
    if not cfg.configured:
        raise RuntimeError("recording needs SC__ODOO__API_KEY in .env (run `just odoo-apikey`)")
    return cfg


@pytest.fixture
def cassette(request: pytest.FixtureRequest) -> Callable[[str], OdooClient]:
    """``cassette("name")`` -> client replaying tests/fixtures/odoo/name.json.

    With ``SC_ODOO_RECORD=1`` (``just odoo-record``) the same call records
    against the live Odoo instead.
    """
    opened: list[OdooClient] = []

    def open_cassette(name: str) -> OdooClient:
        client = cassette_client(FIXTURES / f"{name}.json", record=RECORD, cfg_factory=_live_cfg)
        opened.append(client)
        return client

    yield open_cassette  # type: ignore[misc]


LOGIN_OK = rpc_ok(8)
