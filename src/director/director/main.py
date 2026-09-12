"""ASGI entrypoint for the director service.

Signed events from mail_sync, the scheduler, Odoo and the agents land in
the event inbox and are handed to the orchestration workflow in a
background task. Phase 8 adds the Control Tower API.
"""

from __future__ import annotations

from fastapi import FastAPI
from injector import Module, provider, singleton
from loguru import logger

from director import __version__
from director.agents import Agents, build_agents
from director.conversations import PostgresConversationLookup, PostgresMailActivity
from director.escalation import (
    Escalator,
    LoggingEscalator,
    OdooApprovals,
    OdooEscalationPorts,
    OdooEscalator,
)
from director.handlers.followups import FollowUpJob
from director.inbox import EventInbox, EventResults, PostgresEventInbox, PostgresEventResults
from director.jobs import JobRunner
from director.policies import FollowUpPolicy
from director.routers import events
from director.store import CaseStore, PostgresCaseStore
from director.workflow import Deps, Orchestrator
from sc_core.app import create_application
from sc_core.infra.db import Database
from sc_core.infra.module import ChatClientFactory, DbModule, LlmModule, OdooModule
from sc_core.infra.settings import Settings
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import ActivityRepo, ApprovalRepo, PurchaseOrderRepo

settings = Settings(service_name="director")


class DirectorModule(Module):
    """Explicit providers: injector only auto-constructs ``@inject``-decorated classes."""

    @provider
    @singleton
    def provide_inbox(self, db: Database) -> EventInbox:  # type: ignore[type-abstract]
        return PostgresEventInbox(db)

    @provider
    @singleton
    def provide_results(self, db: Database) -> EventResults:  # type: ignore[type-abstract]
        return PostgresEventResults(db)

    @provider
    @singleton
    def provide_cases(self, db: Database) -> CaseStore:  # type: ignore[type-abstract]
        return PostgresCaseStore(db)

    @provider
    @singleton
    def provide_escalator(
        self,
        settings: Settings,
        cases: CaseStore,  # type: ignore[type-abstract]
        chats: ChatClientFactory,
        approvals: ApprovalRepo,
        activities: ActivityRepo,
        orders: PurchaseOrderRepo,
    ) -> Escalator:  # type: ignore[type-abstract]
        if not settings.odoo.configured:
            return LoggingEscalator()
        ports = OdooEscalationPorts(
            approvals, activities, orders, approver_user_id=settings.agents.approver_user_id
        )
        return OdooEscalator(
            chats.for_agent("director"),
            ports,
            cases,
            deadline_days=settings.agents.approval_deadline_days,
            langfuse=settings.langfuse,
        )

    @provider
    @singleton
    def provide_jobs(
        self,
        settings: Settings,
        orders: PurchaseOrderRepo,
        db: Database,
        cases: CaseStore,  # type: ignore[type-abstract]
        agents: Agents,
        escalator: Escalator,  # type: ignore[type-abstract]
        approvals: ApprovalRepo,
    ) -> JobRunner:  # type: ignore[type-abstract]
        return FollowUpJob(
            policy=FollowUpPolicy.from_settings(settings.director),
            orders=orders,
            mail=PostgresMailActivity(db),
            cases=cases,
            agents=agents,
            escalator=escalator,
            approvals=OdooApprovals(approvals, orders),
            conversations=PostgresConversationLookup(db),
        )

    @provider
    @singleton
    def provide_agents(self, settings: Settings) -> Agents:
        return build_agents(settings)

    @provider
    @singleton
    def provide_deps(
        self,
        cases: CaseStore,  # type: ignore[type-abstract]
        agents: Agents,
        escalator: Escalator,  # type: ignore[type-abstract]
        jobs: JobRunner,  # type: ignore[type-abstract]
        db: Database,
    ) -> Deps:
        return Deps(
            cases=cases,
            agents=agents,
            escalator=escalator,
            jobs=jobs,
            conversations=PostgresConversationLookup(db),
        )

    @provider
    @singleton
    def provide_orchestrator(
        self,
        deps: Deps,
        results: EventResults,  # type: ignore[type-abstract]
    ) -> Orchestrator:
        return Orchestrator(deps, results)


def build_app() -> FastAPI:
    application = create_application(
        settings,
        version=__version__,
        routers=[events.router],
        modules=[DbModule(), OdooModule(), LlmModule(), DirectorModule()],
        startup=[_open_db, _connect_odoo],
        shutdown=[_close_odoo, _close_agents, _close_db],
    )
    return application


async def _open_db() -> None:
    await app.state.injector.get(Database).open()


async def _close_db() -> None:
    await app.state.injector.get(Database).close()


async def _close_agents() -> None:
    await app.state.injector.get(Agents).aclose()


async def _connect_odoo() -> None:
    """Instantiate the Odoo client at startup so its readiness check is registered.

    Without an API key (CI, a fresh checkout) the service still starts and
    simply does not report Odoo; the first real use would fail loudly.
    """
    if not settings.odoo.configured:
        logger.warning("SC__ODOO__API_KEY not set; odoo readiness check not registered")
        return
    app.state.injector.get(OdooClient)


async def _close_odoo() -> None:
    if settings.odoo.configured:
        await app.state.injector.get(OdooClient).aclose()


if not settings.events.configured:
    logger.warning("SC__EVENTS__SIGNING_SECRET not set; /events will reject every request")

app = build_app()
