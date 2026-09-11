"""Fixtures for tests against the real mailbox (delegated session from `just mail-login`).

Skipped, not failed, when no session is cached or the mail settings are
missing, so `just test-int` stays useful without the mailbox.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from sc_core.infra.settings import Settings, reset_settings_cache
from sc_core.mail.auth import DelegatedTokenProvider, PostgresTokenCacheStore
from sc_core.mail.errors import MailAuthRequired
from sc_core.mail.graph import GraphMailClient
from sc_core.shared.errors import ScError


@pytest.fixture(scope="session")
def mail_provider() -> DelegatedTokenProvider:
    reset_settings_cache()
    settings = Settings()
    if not settings.mail.configured or settings.mail.auth_mode != "delegated":
        pytest.skip("SC__MAIL__CLIENT_ID not set or not in delegated mode")
    try:
        provider = DelegatedTokenProvider(
            settings.mail, PostgresTokenCacheStore(str(settings.app_db.dsn))
        )
        asyncio.run(provider.access_token())
    except (MailAuthRequired, ScError, OSError) as exc:
        pytest.skip(f"no usable mail session ({type(exc).__name__}); run `just mail-login`")
    return provider


@pytest.fixture
async def graph_client(mail_provider: DelegatedTokenProvider) -> AsyncIterator[GraphMailClient]:
    async with GraphMailClient(mail_provider) as client:
        yield client
