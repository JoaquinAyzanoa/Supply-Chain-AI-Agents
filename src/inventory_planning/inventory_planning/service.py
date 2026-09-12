"""Build the planner from settings and live dependencies.

``AgentProvider.get()`` builds the graph once, after the database pool is
open (the checkpointer creates its tables on first use), and hands out the
same agent afterwards. Tests bypass it with an in-memory graph.
"""

from __future__ import annotations

import asyncio
from typing import Any

from injector import Module, provider, singleton

from inventory_planning import AGENT_NAME
from inventory_planning.agent import InventoryPlanningAgent
from inventory_planning.graph import Deps, build_graph
from inventory_planning.policy import PostgresParamsStore
from inventory_planning.ports import LiveDataPorts, LiveWritePorts
from inventory_planning.runs import PostgresRunStore
from sc_core.a2a.events import EventPublisher, PostgresOutbox
from sc_core.graph import ApprovalGateway, OdooApprovalPorts, build_checkpointer
from sc_core.infra.db import Database
from sc_core.infra.module import ChatClientFactory
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import Settings
from sc_core.llm import default_budget
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import ActivityRepo, ApprovalRepo


class AgentProvider:
    def __init__(self, settings: Settings, deps: Deps, db: Database) -> None:
        self._settings = settings
        self._deps = deps
        self._db = db
        self._agent: InventoryPlanningAgent | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> InventoryPlanningAgent:
        if self._agent is None:
            async with self._lock:
                if self._agent is None:
                    checkpointer = await build_checkpointer(self._db)
                    self._agent = InventoryPlanningAgent(
                        build_graph(self._deps, checkpointer),
                        model=self._settings.llm.model_for(AGENT_NAME),
                        writes=self._deps.writes,
                        language=self._settings.agents.language,
                        budget=lambda: default_budget(self._settings),
                    )
        return self._agent


class InventoryPlanningModule(Module):
    @provider
    @singleton
    def provide_publisher(self, settings: Settings, db: Database) -> EventPublisher:
        """``rfq.drafted`` after apply and ``agent.run_finished`` after a resume."""
        return EventPublisher(settings.events, outbox=PostgresOutbox(db))

    @provider
    @singleton
    def provide_deps(
        self,
        settings: Settings,
        odoo: OdooClient,
        db: Database,
        chats: ChatClientFactory,
        publisher: EventPublisher,
        runtime: RuntimeSettingsReader,
    ) -> Deps:
        gateway = ApprovalGateway(
            OdooApprovalPorts(ApprovalRepo(odoo), ActivityRepo(odoo)),
            agent_name=AGENT_NAME,
            callback_url=settings.planning.public_url.rstrip("/") + "/approvals/callback",
            callback_secret=settings.events.signing_secret.get_secret_value() or None,
            approver_user_id=settings.agents.approver_user_id,
            deadline_days=settings.agents.approval_deadline_days,
            language=settings.agents.language,
            control_tower_url=settings.ui.public_url,
        )
        return Deps(
            data=LiveDataPorts(odoo),
            writes=LiveWritePorts(odoo),
            params=PostgresParamsStore(db),
            runs=PostgresRunStore(db),
            chat=chats.for_agent(AGENT_NAME),
            approvals=gateway,
            cfg=settings.planning,
            runtime=runtime,
            language=settings.agents.language,
            publish=publisher.publish,
            langfuse=settings.langfuse,
        )

    @provider
    @singleton
    def provide_agent_provider(self, settings: Settings, deps: Deps, db: Database) -> AgentProvider:
        return AgentProvider(settings, deps, db)


def module_list() -> list[Any]:
    """Injector modules the service needs, in dependency order (used by main and tests)."""
    from sc_core.infra.module import DbModule, LlmModule, OdooModule

    return [DbModule(), OdooModule(), LlmModule(), InventoryPlanningModule()]
