"""Fixtures for tests against the real model provider and the compose Langfuse.

Skipped, not failed, when the provider key or Langfuse is missing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from sc_core.infra import tracing
from sc_core.infra.settings import Settings, reset_settings_cache
from sc_core.llm import get_chat_client
from sc_core.llm.client import ChatCompleter
from sc_core.llm.registry import Registry


@pytest.fixture(scope="session")
def llm_settings() -> Settings:
    reset_settings_cache()
    settings = Settings()
    registry = Registry.load()
    provider = registry.provider_for(settings.llm.default_model)
    if not provider.configured:
        pytest.skip(f"{provider.api_key_env} not set in .env; model tests need a real key")
    return settings


@pytest.fixture
async def chat(llm_settings: Settings) -> AsyncIterator[ChatCompleter]:
    tracing.configure_tracing(llm_settings)
    client = get_chat_client("integration_test", settings=llm_settings)
    yield client
    tracing.flush()
    aclose = getattr(client, "aclose", None)
    if aclose:
        await aclose()


@pytest.fixture(scope="session")
def langfuse_settings() -> Settings:
    reset_settings_cache()
    settings = Settings()
    if not settings.langfuse.configured:
        pytest.skip("SC__LANGFUSE__PUBLIC_KEY / SECRET_KEY not set")
    try:
        httpx.get(f"{settings.langfuse.host}/api/public/health", timeout=5).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"Langfuse not reachable at {settings.langfuse.host}; run `just up`")
    return settings
