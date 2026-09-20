"""Build the agent from settings and live dependencies."""

from __future__ import annotations

import asyncio
from typing import Any

from injector import Module, provider, singleton

from sc_core.a2a.client import A2AClient
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
    PartnerRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
)
from sourcing import AGENT_NAME
from sourcing.domain.compare import Weights
from sourcing.graph.builder import Deps, build_graph
from sourcing.graph.nodes.common import limits_from
from sourcing.graph.runner import SourcingAgent
from sourcing.infra.ports import LiveSourcingPorts
from sourcing.infra.store import PostgresRoundStore, RoundStore


class AgentProvider:
    def __init__(self, settings: Settings, deps: Deps, db: Database) -> None:
        self._settings = settings
        self._deps = deps
        self._db = db
        self._agent: SourcingAgent | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> SourcingAgent:
        if self._agent is None:
            async with self._lock:
                if self._agent is None:
                    checkpointer = await build_checkpointer(self._db)
                    self._agent = SourcingAgent(
                        build_graph(self._deps, checkpointer),
                        model=self._settings.llm.model_for(AGENT_NAME),
                        ports=self._deps.ports,
                        language=self._settings.agents.language,
                        budget=lambda: default_budget(self._settings),
                    )
        return self._agent


class SourcingModule(Module):
    @provider
    @singleton
    def provide_store(self, db: Database) -> RoundStore:  # type: ignore[type-abstract]
        return PostgresRoundStore(db)

    @provider
    @singleton
    def provide_ports(
        self,
        settings: Settings,
        odoo: OdooClient,
        store: RoundStore,  # type: ignore[type-abstract]
    ) -> LiveSourcingPorts:
        return LiveSourcingPorts(
            odoo=odoo,
            store=store,
            purchase_orders=PurchaseOrderRepo(odoo),
            partners=PartnerRepo(odoo),
            mail_links=MailLinkRepo(odoo),
            supplier_info=SupplierInfoRepo(odoo),
            agent_runs=AgentRunRepo(odoo),
            supplier_comms=A2AClient(
                settings.a2a.supplier_comms_url,
                token=settings.a2a_token,
                timeout_seconds=settings.a2a.timeout_seconds,
            ),
            performance_url=settings.a2a.supplier_performance_url,
            token=settings.a2a_token,
        )

    @provider
    @singleton
    def provide_deps(
        self,
        settings: Settings,
        ports: LiveSourcingPorts,
        odoo: OdooClient,
        chats: ChatClientFactory,
        runtime: RuntimeSettingsReader,
        db: Database,
    ) -> Deps:
        cfg = settings.sourcing
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
            limits=limits_from(runtime),
            weights=Weights(
                price=cfg.weight_price,
                lead_time=cfg.weight_lead_time,
                score=cfg.weight_score,
                incomplete_penalty=cfg.incomplete_penalty,
            ),
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

    return [DbModule(), OdooModule(), LlmModule(), SourcingModule()]
