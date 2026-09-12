"""Fixtures for the end-to-end mail sync tests.

Real Microsoft Graph session (``just mail-login``) and real Odoo (``just
up``), but a throwaway Postgres for the sync state and an in-process
director, so nothing of the developer's app database is touched and the
event delivery is still proven against the real signature check.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from injector import Binder, Module, singleton
from pydantic import SecretStr

from director.inbox import EventInbox, MemoryEventInbox
from director.routers import events as director_events
from mail_sync.ports import OdooPorts
from mail_sync.state import PostgresSyncState
from mail_sync.sync import SyncRunner
from sc_core.a2a.events import EventPublisher, PostgresOutbox
from sc_core.app import create_application
from sc_core.infra.db import Database
from sc_core.infra.locks import MemoryLock
from sc_core.infra.migrate import apply_migrations
from sc_core.infra.settings import AppDbCfg, EventsCfg, MailSyncCfg, Settings, reset_settings_cache
from sc_core.mail.auth import DelegatedTokenProvider, PostgresTokenCacheStore
from sc_core.mail.errors import MailAuthRequired
from sc_core.mail.graph import GraphMailClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import MailLinkRepo, PartnerRepo, PurchaseOrderRepo
from sc_core.shared.errors import ScError

MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"
SECRET = "e2e-signing-secret"


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
    apply_migrations(fresh_postgres_dsn, MIGRATIONS)
    database = Database(AppDbCfg(dsn=fresh_postgres_dsn))  # type: ignore[arg-type]
    await database.open()
    yield database
    await database.close()


class _InboxModule(Module):
    def __init__(self, inbox: MemoryEventInbox) -> None:
        self.inbox = inbox

    def configure(self, binder: Binder) -> None:
        binder.bind(EventInbox, to=self.inbox, scope=singleton)  # type: ignore[type-abstract]


@pytest.fixture
def director_inbox() -> MemoryEventInbox:
    return MemoryEventInbox()


@pytest.fixture
async def world(
    graph: GraphMailClient,
    odoo: OdooClient,
    db: Database,
    director_inbox: MemoryEventInbox,
) -> AsyncIterator[dict[str, Any]]:
    """A SyncRunner on the real mailbox and Odoo, publishing to an in-process director."""
    director_settings = Settings(
        _env_file=None,
        service_name="director",
        environment="test",
        events={"signing_secret": SecretStr(SECRET)},
    )
    director_app = create_application(
        director_settings, routers=[director_events.router], modules=[_InboxModule(director_inbox)]
    )
    state = PostgresSyncState(db)
    publisher = EventPublisher(
        EventsCfg(signing_secret=SecretStr(SECRET), director_url="http://director.test"),
        outbox=PostgresOutbox(db),
        http=httpx.AsyncClient(transport=httpx.ASGITransport(app=director_app)),
    )
    ports = OdooPorts(
        purchase_orders=PurchaseOrderRepo(odoo),
        mail_links=MailLinkRepo(odoo),
        partners=PartnerRepo(odoo),
        state=state,
    )
    runner = SyncRunner(
        graph=graph,
        state=state,
        ports=ports,
        publisher=publisher,
        lock=MemoryLock(),
        cfg=MailSyncCfg(),
        mailbox="me",
    )
    # Start from "now": only messages arriving after this point are synced.
    baseline = await graph.inbox_delta(None)
    await state.save_delta_link("me", baseline.delta_link, status="baseline")
    yield {
        "runner": runner,
        "state": state,
        "links": MailLinkRepo(odoo),
        "pos": PurchaseOrderRepo(odoo),
        "baseline_delta": baseline.delta_link,
    }
    await publisher.aclose()
