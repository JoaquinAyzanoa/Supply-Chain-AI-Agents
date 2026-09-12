"""Fixtures for integration tests (require Docker).

Throwaway Postgres and Redis come from testcontainers. The "live" fixtures
talk to the compose Odoo and the real bot mailbox and skip, not fail, when
those are not available, so `just test-int` stays useful anywhere.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from sc_core.infra.db import Database
from sc_core.infra.migrate import apply_migrations
from sc_core.infra.settings import AppDbCfg, Settings, reset_settings_cache
from sc_core.mail.auth import DelegatedTokenProvider, PostgresTokenCacheStore
from sc_core.mail.errors import MailAuthRequired
from sc_core.mail.graph import GraphMailClient
from sc_core.odoo.client import OdooClient
from sc_core.shared.errors import ScError

# Testcontainers starts a "Ryuk" reaper container to clean up after tests. On
# Docker Desktop for Windows its port mapping is often unavailable, which
# aborts every container start. Containers are context-managed below, so the
# reaper is not needed; disable it unless the caller decided otherwise.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """A throwaway Postgres 16 started with testcontainers."""
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # older testcontainers layout
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver=None) as pg:
        yield pg.get_connection_url()


@pytest.fixture
def fresh_postgres_dsn(postgres_dsn: str) -> Iterator[str]:
    """A brand-new database on the session container, so tests never share state."""
    import psycopg
    from psycopg import sql

    name = f"t_{uuid4().hex[:12]}"
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    base = postgres_dsn.rsplit("/", 1)[0]
    yield f"{base}/{name}"
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def redis_dsn() -> Iterator[str]:
    try:
        from testcontainers.community.redis import RedisContainer
    except ImportError:
        from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as rc:
        host = rc.get_container_host_ip()
        port = rc.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


# --- live services: compose Odoo and the real mailbox -------------------------------


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    reset_settings_cache()
    settings = Settings()
    if not settings.mail.configured or settings.mail.auth_mode != "delegated":
        pytest.skip("SC__MAIL__CLIENT_ID not set or not in delegated mode")
    if not settings.odoo.configured:
        pytest.skip("SC__ODOO__API_KEY not set; run `just odoo-apikey`")
    try:
        httpx.get(f"{settings.odoo.url}/web/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"Odoo not reachable at {settings.odoo.url}; run `just up`")
    return settings


@pytest.fixture(scope="session")
def mail_provider(live_settings: Settings) -> DelegatedTokenProvider:
    try:
        provider = DelegatedTokenProvider(
            live_settings.mail, PostgresTokenCacheStore(str(live_settings.app_db.dsn))
        )
        asyncio.run(provider.access_token())
    except (MailAuthRequired, ScError, OSError) as exc:
        pytest.skip(f"no usable mail session ({type(exc).__name__}); run `just mail-login`")
    return provider


@pytest.fixture
async def graph(mail_provider: DelegatedTokenProvider) -> AsyncIterator[GraphMailClient]:
    async with GraphMailClient(mail_provider) as client:
        yield client


@pytest.fixture
async def live_db(live_settings: Settings) -> AsyncIterator[Database]:
    """The compose application database (not the throwaway one), for end-to-end checks."""
    database = Database(live_settings.app_db)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
async def odoo(live_settings: Settings) -> AsyncIterator[OdooClient]:
    async with OdooClient(live_settings.odoo) as client:
        yield client


@pytest.fixture
async def odoo_admin(live_settings: Settings) -> AsyncIterator[OdooClient]:
    cfg = live_settings.odoo.model_copy(update={"login": "admin", "api_key": SecretStr("admin")})
    async with OdooClient(cfg) as client:
        yield client


@pytest.fixture
async def db(fresh_postgres_dsn: str) -> AsyncIterator[Database]:
    """A migrated, open application database on the throwaway Postgres."""
    apply_migrations(fresh_postgres_dsn, MIGRATIONS)
    database = Database(AppDbCfg(dsn=fresh_postgres_dsn))  # type: ignore[arg-type]
    await database.open()
    yield database
    await database.close()
