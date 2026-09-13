"""Build the agent from settings and live dependencies.

The supplier agent's ``LivePorts`` is built here too and wrapped: same
mail client, same repositories, plus the receipt reads and the writes
with source ``tracking``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from injector import Module, provider, singleton

from logistics import AGENT_NAME
from logistics.agent import LogisticsAgent
from logistics.graph import Deps, build_graph
from logistics.ports import LiveLogisticsPorts
from sc_core.a2a.events import EventPublisher, PostgresOutbox
from sc_core.graph import ApprovalGateway, OdooApprovalPorts, build_checkpointer
from sc_core.infra.db import Database
from sc_core.infra.module import ChatClientFactory
from sc_core.infra.settings import Settings
from sc_core.llm import default_budget
from sc_core.mail.outbound import PostgresOutboundMailStore
from sc_core.mail.protocol import MailClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import (
    ActivityRepo,
    AgentRunRepo,
    ApprovalRepo,
    MailLinkRepo,
    PartnerRepo,
    PickingRepo,
    PurchaseOrderRepo,
    StockMoveLineRepo,
    SupplierInfoRepo,
)
from supplier_comms.ports import LivePorts


class AgentProvider:
    def __init__(self, settings: Settings, deps: Deps, db: Database) -> None:
        self._settings = settings
        self._deps = deps
        self._db = db
        self._agent: LogisticsAgent | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> LogisticsAgent:
        if self._agent is None:
            async with self._lock:
                if self._agent is None:
                    checkpointer = await build_checkpointer(self._db)
                    self._agent = LogisticsAgent(
                        build_graph(self._deps, checkpointer),
                        model=self._settings.llm.model_for(AGENT_NAME),
                        ports=self._deps.ports,
                        language=self._settings.agents.language,
                        budget=lambda: default_budget(self._settings),
                    )
        return self._agent


class LogisticsModule(Module):
    @provider
    @singleton
    def provide_ports(
        self,
        odoo: OdooClient,
        db: Database,
        graph: MailClient,  # type: ignore[type-abstract]
        settings: Settings,
    ) -> LiveLogisticsPorts:
        base = LivePorts(
            odoo=odoo,
            purchase_orders=PurchaseOrderRepo(odoo),
            partners=PartnerRepo(odoo),
            mail_links=MailLinkRepo(odoo),
            supplier_info=SupplierInfoRepo(odoo),
            agent_runs=AgentRunRepo(odoo),
            graph=graph,
            outbound=PostgresOutboundMailStore(db),
            pdf_max_pages=settings.mail.pdf_max_pages,
            pdf_max_bytes=settings.mail.pdf_max_bytes,
        )
        return LiveLogisticsPorts(
            base,
            pickings=PickingRepo(odoo),
            move_lines=StockMoveLineRepo(odoo),
            purchase_orders=PurchaseOrderRepo(odoo),
            agent_runs=AgentRunRepo(odoo),
        )

    @provider
    @singleton
    def provide_deps(
        self,
        settings: Settings,
        ports: LiveLogisticsPorts,
        odoo: OdooClient,
        chats: ChatClientFactory,
    ) -> Deps:
        gateway = ApprovalGateway(
            OdooApprovalPorts(ApprovalRepo(odoo), ActivityRepo(odoo)),
            agent_name=AGENT_NAME,
            callback_url=settings.logistics.public_url.rstrip("/") + "/approvals/callback",
            callback_secret=settings.events.signing_secret.get_secret_value() or None,
            approver_user_id=settings.agents.approver_user_id,
            deadline_days=settings.agents.approval_deadline_days,
            language=settings.agents.language,
            control_tower_url=settings.ui.public_url,
        )
        return Deps(
            ports=ports,
            chat=chats.for_agent(AGENT_NAME),
            approvals=gateway,
            language=settings.agents.language,
            tolerance_pct=settings.logistics.receipt_tolerance_pct,
            max_attachment_chars=settings.logistics.max_attachment_chars,
            langfuse=settings.langfuse,
        )

    @provider
    @singleton
    def provide_agent_provider(self, settings: Settings, deps: Deps, db: Database) -> AgentProvider:
        return AgentProvider(settings, deps, db)

    @provider
    @singleton
    def provide_publisher(self, settings: Settings, db: Database) -> EventPublisher:
        return EventPublisher(settings.events, outbox=PostgresOutbox(db))


def module_list() -> list[Any]:
    """Injector modules the service needs, in dependency order (used by main and tests)."""
    from sc_core.infra.module import DbModule, LlmModule, MailModule, OdooModule

    return [DbModule(), OdooModule(), MailModule(critical=False), LlmModule(), LogisticsModule()]
