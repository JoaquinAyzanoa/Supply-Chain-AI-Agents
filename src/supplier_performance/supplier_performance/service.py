"""Build the agent from settings and live dependencies."""

from __future__ import annotations

import asyncio
from typing import Any

from injector import Module, provider, singleton

from sc_core.a2a.events import EventPublisher, PostgresOutbox
from sc_core.graph import (
    ApprovalGateway,
    OdooApprovalPorts,
    PostgresAutoActions,
    build_checkpointer,
    policy_from,
)
from sc_core.infra.db import Database
from sc_core.infra.module import ChatClientFactory
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import Settings
from sc_core.llm import default_budget
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import (
    ActivityRepo,
    AgentRunRepo,
    ApprovalRepo,
    MailLinkRepo,
    PurchaseOrderRepo,
    StockMoveLineRepo,
    SupplierInfoRepo,
)
from supplier_performance import AGENT_NAME
from supplier_performance.agent import SupplierPerformanceAgent
from supplier_performance.graph import Deps, build_graph
from supplier_performance.metrics import Weights
from supplier_performance.ports import LivePerformancePorts


class AgentProvider:
    def __init__(self, settings: Settings, deps: Deps, db: Database) -> None:
        self._settings = settings
        self._deps = deps
        self._db = db
        self._agent: SupplierPerformanceAgent | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> SupplierPerformanceAgent:
        if self._agent is None:
            async with self._lock:
                if self._agent is None:
                    checkpointer = await build_checkpointer(self._db)
                    self._agent = SupplierPerformanceAgent(
                        build_graph(self._deps, checkpointer),
                        model=self._settings.llm.model_for(AGENT_NAME),
                        ports=self._deps.ports,
                        language=self._settings.agents.language,
                        budget=lambda: default_budget(self._settings),
                    )
        return self._agent


class SupplierPerformanceModule(Module):
    @provider
    @singleton
    def provide_ports(self, odoo: OdooClient, db: Database) -> LivePerformancePorts:
        return LivePerformancePorts(
            odoo=odoo,
            db=db,
            purchase_orders=PurchaseOrderRepo(odoo),
            move_lines=StockMoveLineRepo(odoo),
            mail_links=MailLinkRepo(odoo),
            supplier_info=SupplierInfoRepo(odoo),
            agent_runs=AgentRunRepo(odoo),
        )

    @provider
    @singleton
    def provide_deps(
        self,
        settings: Settings,
        ports: LivePerformancePorts,
        odoo: OdooClient,
        chats: ChatClientFactory,
        runtime: RuntimeSettingsReader,
        db: Database,
    ) -> Deps:
        cfg = settings.supplier_performance
        gateway = ApprovalGateway(
            OdooApprovalPorts(ApprovalRepo(odoo), ActivityRepo(odoo)),
            agent_name=AGENT_NAME,
            callback_url=cfg.public_url.rstrip("/") + "/approvals/callback",
            callback_secret=settings.events.signing_secret.get_secret_value() or None,
            approver_user_id=settings.agents.approver_user_id,
            deadline_days=settings.agents.approval_deadline_days,
            language=settings.agents.language,
            control_tower_url=settings.ui.public_url,
            policy=policy_from(runtime),
            auto_actions=PostgresAutoActions(db),
        )
        return Deps(
            ports=ports,
            chat=chats.for_agent(AGENT_NAME),
            approvals=gateway,
            weights=Weights(
                otif=cfg.weight_otif,
                lead_time=cfg.weight_lead_time,
                promise=cfg.weight_promise,
                response=cfg.weight_response,
                quality=cfg.weight_quality,
                price=cfg.weight_price,
            ),
            months=cfg.months,
            max_suppliers=cfg.max_suppliers,
            language=settings.agents.language,
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
    from sc_core.infra.module import DbModule, LlmModule, OdooModule

    return [DbModule(), OdooModule(), LlmModule(), SupplierPerformanceModule()]
