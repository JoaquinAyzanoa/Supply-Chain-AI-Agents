"""Injector wiring for the mail_sync service and its CLI."""

from __future__ import annotations

from injector import Module, provider, singleton

from mail_sync.ports import OdooPorts
from mail_sync.state import PostgresSyncState, SyncState
from mail_sync.sync import SyncRunner
from sc_core.a2a.events import EventPublisher, PostgresOutbox
from sc_core.infra.db import Database
from sc_core.infra.locks import RedisLock
from sc_core.infra.module import AsyncRedis
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import Settings
from sc_core.mail.protocol import MailClient
from sc_core.odoo.repositories import MailLinkRepo, PartnerRepo, PurchaseOrderRepo


class MailSyncModule(Module):
    @provider
    @singleton
    def provide_state(self, db: Database) -> SyncState:  # type: ignore[type-abstract]
        return PostgresSyncState(db)

    @provider
    @singleton
    def provide_publisher(self, settings: Settings, db: Database) -> EventPublisher:
        return EventPublisher(settings.events, outbox=PostgresOutbox(db))

    @provider
    @singleton
    def provide_runner(
        self,
        settings: Settings,
        graph: MailClient,  # type: ignore[type-abstract]
        state: SyncState,  # type: ignore[type-abstract]
        publisher: EventPublisher,
        redis: AsyncRedis,
        purchase_orders: PurchaseOrderRepo,
        mail_links: MailLinkRepo,
        partners: PartnerRepo,
        runtime: RuntimeSettingsReader,
    ) -> SyncRunner:
        ports = OdooPorts(
            purchase_orders=purchase_orders, mail_links=mail_links, partners=partners, state=state
        )
        return SyncRunner(
            runtime=runtime,
            graph=graph,
            state=state,
            ports=ports,
            publisher=publisher,
            lock=RedisLock(redis),
            cfg=settings.mail_sync,
            mailbox=settings.mail.mailbox,
        )
