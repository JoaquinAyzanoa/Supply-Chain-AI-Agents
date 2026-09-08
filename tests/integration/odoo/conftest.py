"""Fixtures for tests against the Odoo container started with `just up`.

They read the same .env the services use (SC__ODOO__*). When Odoo is not
reachable or no API key is configured the tests are skipped, not failed, so
`just test-int` stays useful on a machine without Odoo running.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from sc_core.infra.settings import Settings, reset_settings_cache
from sc_core.odoo.client import OdooClient


@pytest.fixture(scope="session")
def odoo_settings() -> Settings:
    reset_settings_cache()
    settings = Settings()
    if not settings.odoo.configured:
        pytest.skip("SC__ODOO__API_KEY not set; run `just odoo-apikey`")
    try:
        httpx.get(f"{settings.odoo.url}/web/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"Odoo not reachable at {settings.odoo.url}; run `just up`")
    return settings


@pytest.fixture
async def odoo_client(odoo_settings: Settings) -> AsyncIterator[OdooClient]:
    async with OdooClient(odoo_settings.odoo) as client:
        yield client
