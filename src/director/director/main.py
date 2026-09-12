"""ASGI entrypoint for the director service.

Signed events from mail_sync, the scheduler, Odoo and the agents land in
the event inbox and are handed to the orchestration workflow in a
background task. Phase 8 adds the Control Tower API under ``/api`` and
serves the built frontend under ``/`` when the bundle exists.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from injector import Injector, Module, provider, singleton
from loguru import logger

from director import __version__
from director.agents import Agents, build_agents
from director.api import api_router
from director.api.approvals import ApprovalsGateway, OdooApprovalsGateway
from director.api.auth import LoginRateLimit, PostgresUserStore, UserStore
from director.api.chat import (
    CaseAssistant,
    ChatActions,
    EmailReader,
    GraphEmailReader,
    NoEmailReader,
)
from director.api.exceptions import ExceptionsSource
from director.api.planning import (
    DemandSource,
    HttpDemandSource,
    PlanningReadStore,
    PostgresPlanningReadStore,
)
from director.api.runs import PostgresSchedulerRuns, RunsGateway, SchedulerRuns
from director.api.settings import PostgresRuntimeSettingsStore, RuntimeSettingsStore
from director.concurrency import PoLocks
from director.conversations import PostgresConversationLookup, PostgresMailActivity
from director.escalation import (
    Escalator,
    LoggingEscalator,
    OdooApprovals,
    OdooEscalationPorts,
    OdooEscalator,
)
from director.handlers.followups import FollowUpJob
from director.handlers.planning import JobDispatcher, PlanningJob
from director.inbox import EventInbox, EventResults, PostgresEventInbox, PostgresEventResults
from director.jobs import JobRunner
from director.policies import FollowUpPolicy
from director.realtime import BroadcastingCaseStore
from director.routers import events
from director.store import CaseStore, PostgresCaseStore
from director.workflow import Deps, Orchestrator
from sc_core.app import create_application
from sc_core.app.realtime import Realtime, RedisRealtime
from sc_core.app.static import mount_spa
from sc_core.infra.db import Database
from sc_core.infra.locks import RedisLock
from sc_core.infra.module import (
    AsyncRedis,
    ChatClientFactory,
    DbModule,
    LlmModule,
    MailModule,
    OdooModule,
    RedisModule,
)
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import Settings
from sc_core.mail.protocol import MailClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import (
    ActivityRepo,
    AgentRunRepo,
    ApprovalRepo,
    PurchaseOrderRepo,
)

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
    def provide_realtime(self, redis: AsyncRedis) -> Realtime:  # type: ignore[type-abstract]
        return RedisRealtime(redis)

    @provider
    @singleton
    def provide_cases(self, db: Database, realtime: Realtime) -> CaseStore:  # type: ignore[type-abstract]
        return BroadcastingCaseStore(PostgresCaseStore(db), realtime)

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
            language=settings.agents.language,
            control_tower_url=settings.ui.public_url,
        )

    @provider
    @singleton
    def provide_followups(
        self,
        settings: Settings,
        orders: PurchaseOrderRepo,
        db: Database,
        cases: CaseStore,  # type: ignore[type-abstract]
        agents: Agents,
        escalator: Escalator,  # type: ignore[type-abstract]
        approvals: ApprovalRepo,
        runtime: RuntimeSettingsReader,
    ) -> FollowUpJob:
        return FollowUpJob(
            policy=FollowUpPolicy.from_settings(settings.director),
            orders=orders,
            mail=PostgresMailActivity(db),
            cases=cases,
            agents=agents,
            escalator=escalator,
            approvals=OdooApprovals(approvals, orders, language=settings.agents.language),
            conversations=PostgresConversationLookup(db),
            language=settings.agents.language,
            runtime=runtime,
        )

    @provider
    @singleton
    def provide_exceptions(self, followups: FollowUpJob) -> ExceptionsSource:  # type: ignore[type-abstract]
        return followups

    @provider
    @singleton
    def provide_case_assistant(
        self,
        settings: Settings,
        chats: ChatClientFactory,
        cases: CaseStore,  # type: ignore[type-abstract]
        approvals: ApprovalsGateway,  # type: ignore[type-abstract]
        orders: PurchaseOrderRepo,
        followups: FollowUpJob,
        emails: EmailReader,  # type: ignore[type-abstract]
    ) -> CaseAssistant:
        return CaseAssistant(
            chats.for_agent("director"),
            cases=cases,
            approvals=approvals,
            orders=orders,
            policy=followups,
            emails=emails,
            language=settings.agents.language,
            langfuse=settings.langfuse,
        )

    @provider
    @singleton
    def provide_email_reader(self, settings: Settings, injector: Injector) -> EmailReader:  # type: ignore[type-abstract]
        """Reads an email only when a person asks about it; nothing without a mailbox."""
        if not settings.mail.configured:
            logger.info("mail not configured; the case assistant answers without email text")
            return NoEmailReader()
        return GraphEmailReader(injector.get(MailClient))  # type: ignore[type-abstract]

    @provider
    @singleton
    def provide_chat_actions(
        self,
        deps: Deps,
        approvals: ApprovalsGateway,  # type: ignore[type-abstract]
    ) -> ChatActions:
        return ChatActions(deps, approvals)

    @provider
    @singleton
    def provide_jobs(
        self,
        db: Database,
        cases: CaseStore,  # type: ignore[type-abstract]
        agents: Agents,
        escalator: Escalator,  # type: ignore[type-abstract]
        followups: FollowUpJob,
    ) -> JobRunner:  # type: ignore[type-abstract]
        return JobDispatcher(
            {
                "po_followups": followups,
                "inventory_planning": PlanningJob(
                    cases=cases,
                    agents=agents,
                    escalator=escalator,
                    conversations=PostgresConversationLookup(db),
                ),
            }
        )

    @provider
    @singleton
    def provide_runs_gateway(self, runs: AgentRunRepo) -> RunsGateway:  # type: ignore[type-abstract]
        return runs

    @provider
    @singleton
    def provide_scheduler_runs(self, db: Database) -> SchedulerRuns:  # type: ignore[type-abstract]
        return PostgresSchedulerRuns(db)

    @provider
    @singleton
    def provide_planning_reads(self, db: Database) -> PlanningReadStore:  # type: ignore[type-abstract]
        return PostgresPlanningReadStore(db)

    @provider
    @singleton
    def provide_demand_source(self, settings: Settings) -> DemandSource:  # type: ignore[type-abstract]
        return HttpDemandSource(settings.a2a.inventory_planning_url, settings.a2a_token)

    @provider
    @singleton
    def provide_agents(self, settings: Settings) -> Agents:
        return build_agents(settings)

    @provider
    @singleton
    def provide_users(self, db: Database) -> UserStore:  # type: ignore[type-abstract]
        return PostgresUserStore(db)

    @provider
    @singleton
    def provide_approvals_gateway(self, approvals: ApprovalRepo) -> ApprovalsGateway:  # type: ignore[type-abstract]
        return OdooApprovalsGateway(approvals)

    @provider
    @singleton
    def provide_runtime_settings(self, db: Database) -> RuntimeSettingsStore:  # type: ignore[type-abstract]
        return PostgresRuntimeSettingsStore(db)

    @provider
    @singleton
    def provide_login_limit(self, settings: Settings) -> LoginRateLimit:
        return LoginRateLimit(settings.ui.login_rate_per_minute)

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
        settings: Settings,
        deps: Deps,
        results: EventResults,  # type: ignore[type-abstract]
        inbox: EventInbox,  # type: ignore[type-abstract]
        redis: AsyncRedis,
        orders: PurchaseOrderRepo,
    ) -> Orchestrator:
        locks = PoLocks(
            RedisLock(redis),
            ttl_seconds=settings.director.lock_ttl_seconds,
            wait_seconds=settings.director.lock_wait_seconds,
        )
        return Orchestrator(
            deps,
            results,
            inbox=inbox,
            locks=locks,
            orders=orders,
            reconcile_since_days=settings.director.reconcile_since_days,
        )


def build_app() -> FastAPI:
    application = create_application(
        settings,
        version=__version__,
        routers=[events.router, api_router],
        modules=[
            DbModule(),
            RedisModule(),
            OdooModule(),
            MailModule(critical=False),
            LlmModule(),
            DirectorModule(),
        ],
        startup=[_open_db, _connect_odoo],
        shutdown=[_close_odoo, _close_agents, _close_db],
    )
    # The Control Tower bundle, when built (docker/director.Dockerfile or `just ui-build`).
    mount_spa(application, Path(settings.ui.static_dir))
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
