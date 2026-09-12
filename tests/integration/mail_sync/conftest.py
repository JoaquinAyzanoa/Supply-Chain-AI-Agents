"""Fixtures for the end-to-end mail sync tests.

Real Microsoft Graph session (``just mail-login``) and real Odoo (``just
up``) from the package-level fixtures, a throwaway Postgres for the sync
state and an in-process director, so nothing of the developer's app
database is touched and the event delivery is still proven against the
real signature check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from director.inbox import MemoryEventInbox
from director.routers import events as director_events
from director.testing import MemoryDirectorModule
from mail_sync.ports import OdooPorts
from mail_sync.state import PostgresSyncState
from mail_sync.sync import SyncRunner
from sc_core.a2a.events import EventPublisher, PostgresOutbox
from sc_core.app import create_application
from sc_core.infra.db import Database
from sc_core.infra.locks import MemoryLock
from sc_core.infra.settings import EventsCfg, MailSyncCfg, Settings
from sc_core.mail.graph import GraphMailClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import MailLinkRepo, PartnerRepo, PurchaseOrderRepo

SECRET = "e2e-signing-secret"


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
        director_settings,
        routers=[director_events.router],
        modules=[MemoryDirectorModule(director_inbox)],
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
