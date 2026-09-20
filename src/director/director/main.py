"""ASGI entrypoint for the director service.

Signed events from mail_sync, the scheduler, Odoo and the agents land in
the event inbox and are handed to the orchestration workflow in a
background task. Phase 8 adds the Control Tower API under ``/api`` and
serves the built frontend under ``/`` when the bundle exists.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI
from injector import Injector, Module, provider, singleton
from loguru import logger

from director import __version__
from director.api import api_router
from director.api.approvals import ApprovalsGateway, OdooApprovalsGateway
from director.api.auth import LoginRateLimit, PostgresUserStore, UserStore
from director.api.board import BoardMoves, BoardOrders, OdooBoardOrders
from director.api.chat import (
    CaseAssistant,
    ChatActions,
    EmailReader,
    GraphEmailReader,
    NoEmailReader,
)
from director.api.demo import DirectorApprovalResolver
from director.api.exceptions import ExceptionsSource
from director.api.mailbox import HttpMailboxSync, MailboxSync
from director.api.performance import HttpPerformanceSource, PerformanceSource
from director.api.planning import (
    DemandSource,
    HttpDemandSource,
    PlanningReadStore,
    PostgresPlanningReadStore,
)
from director.api.risk import HttpRiskSource, RiskSource
from director.api.runs import PostgresSchedulerRuns, RunsGateway, SchedulerRuns
from director.api.settings import PostgresRuntimeSettingsStore, RuntimeSettingsStore
from director.api.suppliers import MailLinks, SupplierPrices
from director.desk.assistant import (
    AssistantStore,
    DepartmentAssistant,
    PlanRunner,
    PostgresAssistantStore,
)
from director.desk.autonomy import (
    AutoActionsStore,
    AutonomyChanges,
    OdooReverter,
    PostgresAutoActionsStore,
    Reverter,
)
from director.desk.briefing import (
    BriefingBuilder,
    BriefingJob,
    BriefingStore,
    PostgresBriefingStore,
)
from director.desk.demo import (
    DemoDirector,
    DemoStore,
    OdooDemoWorld,
    PostgresDemoStore,
    SmtpSupplierMailbox,
    SupplierMailbox,
)
from director.desk.learning import (
    CalibrationJob,
    FeedbackRecorder,
    FeedbackStore,
    PostgresFeedbackStore,
    PostgresSuggestionStore,
    SuggestionStore,
)
from director.desk.sourcing import HttpSourcingSource, SourcingDispatcher, SourcingSource
from director.handlers.followups import FollowUpJob, MailActivity
from director.handlers.jobs import PlaybooksJob, SourcingJob
from director.handlers.performance import PerformanceJob
from director.handlers.planning import JobDispatcher, PlanningJob
from director.notifications.push import PostgresPushStore, PushRelay, PushStore, WebPushSender
from director.notifications.realtime import BroadcastingCaseStore
from director.notifications.teams import TeamsNotifier
from director.orchestration.agents import Agents, build_agents
from director.orchestration.concurrency import PoLocks
from director.orchestration.conversations import PostgresConversationLookup, PostgresMailActivity
from director.orchestration.escalation import (
    Escalator,
    LoggingEscalator,
    OdooApprovals,
    OdooEscalationPorts,
    OdooEscalator,
)
from director.orchestration.inbox import (
    EventInbox,
    EventResults,
    PostgresEventInbox,
    PostgresEventResults,
)
from director.orchestration.jobs import JobRunner
from director.orchestration.policies import FollowUpPolicy
from director.orchestration.store import CaseStore, PostgresCaseStore
from director.orchestration.workflow import Deps, Orchestrator
from director.playbooks import PlaybookEngine, PlaybookStore, PostgresPlaybookStore
from director.routers import events
from sc_core.a2a.events import HmacSigner
from sc_core.app import create_application
from sc_core.app.realtime import Realtime, RedisRealtime
from sc_core.app.static import mount_spa
from sc_core.graph import policy_from
from sc_core.infra.calendar import CalendarStore, PostgresCalendarStore
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
from sc_core.infra.profiles import PostgresProfileStore, ProfileStore
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import Settings
from sc_core.mail.protocol import MailClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import (
    ActivityRepo,
    AgentRunRepo,
    ApprovalRepo,
    MailLinkRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
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
    def provide_mail_activity(self, db: Database) -> MailActivity:  # type: ignore[type-abstract]
        return PostgresMailActivity(db)

    @provider
    @singleton
    def provide_board_orders(self, orders: PurchaseOrderRepo) -> BoardOrders:  # type: ignore[type-abstract]
        return OdooBoardOrders(orders)

    @provider
    @singleton
    def provide_board_moves(
        self,
        orders: BoardOrders,  # type: ignore[type-abstract]
        deps: Deps,
    ) -> BoardMoves:
        return BoardMoves(orders, deps)

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
    def provide_supplier_prices(self, odoo: OdooClient) -> SupplierPrices:  # type: ignore[type-abstract]
        return SupplierInfoRepo(odoo)

    @provider
    @singleton
    def provide_mail_links(self, odoo: OdooClient) -> MailLinks:  # type: ignore[type-abstract]
        return MailLinkRepo(odoo)

    @provider
    @singleton
    def provide_push_store(self, db: Database) -> PushStore:  # type: ignore[type-abstract]
        return PostgresPushStore(db)

    @provider
    @singleton
    def provide_push_relay(
        self,
        settings: Settings,
        realtime: Realtime,  # type: ignore[type-abstract]
        store: PushStore,  # type: ignore[type-abstract]
        approvals: ApprovalsGateway,  # type: ignore[type-abstract]
    ) -> PushRelay:
        return PushRelay(
            realtime=realtime,
            store=store,
            sender=WebPushSender(
                private_key=settings.ui.vapid_private_key.get_secret_value(),
                subject=settings.ui.vapid_subject,
            ),
            approvals=approvals,
            control_tower_url=settings.ui.public_url,
        )

    @provider
    @singleton
    def provide_briefing_store(self, db: Database) -> BriefingStore:  # type: ignore[type-abstract]
        return PostgresBriefingStore(db)

    @provider
    @singleton
    def provide_demo_store(self, db: Database) -> DemoStore:  # type: ignore[type-abstract]
        return PostgresDemoStore(db)

    @provider
    @singleton
    def provide_demo_director(
        self,
        settings: Settings,
        store: DemoStore,  # type: ignore[type-abstract]
        odoo: OdooClient,
        deps: Deps,
        approvals: ApprovalsGateway,  # type: ignore[type-abstract]
        cases: CaseStore,  # type: ignore[type-abstract]
        mailbox_sync: MailboxSync,  # type: ignore[type-abstract]
        risk: RiskSource,  # type: ignore[type-abstract]
        sourcing: SourcingDispatcher,
        sourcing_source: SourcingSource,  # type: ignore[type-abstract]
        briefing: BriefingJob,
        autonomy: AutonomyChanges,
        feedback: FeedbackRecorder,
    ) -> DemoDirector:
        """Demo mode: the world side needs a second Odoo login (stock and accounting rights)
        and the supplier's mailbox needs SMTP credentials; both local only, both optional."""
        cfg = settings.demo
        admin: OdooClient | None = None
        if cfg.world_configured:
            admin = OdooClient(
                settings.odoo.model_copy(
                    update={"login": cfg.odoo_login, "api_key": cfg.odoo_api_key}
                )
            )
            _demo_clients.append(admin)
        mailbox: SupplierMailbox | None = (
            SmtpSupplierMailbox(cfg) if cfg.mailbox_configured else None
        )
        return DemoDirector(
            cfg=cfg,
            store=store,
            world=OdooDemoWorld(odoo, admin),
            mailbox=mailbox,
            deps=deps,
            approvals=approvals,
            cases=cases,
            mailbox_sync=mailbox_sync,
            risk=risk,
            sourcing=sourcing,
            sourcing_source=sourcing_source,
            briefing=briefing,
            resolver=DirectorApprovalResolver(approvals, cases, autonomy, feedback),
        )

    @provider
    @singleton
    def provide_briefing_job(
        self,
        settings: Settings,
        chats: ChatClientFactory,
        cases: CaseStore,  # type: ignore[type-abstract]
        approvals: ApprovalsGateway,  # type: ignore[type-abstract]
        auto_actions: AutoActionsStore,  # type: ignore[type-abstract]
        followups: FollowUpJob,
        store: BriefingStore,  # type: ignore[type-abstract]
        risk: RiskSource,  # type: ignore[type-abstract]
        playbooks: PlaybookEngine,
        runtime: RuntimeSettingsReader,
        injector: Injector,
    ) -> BriefingJob:
        builder = BriefingBuilder(
            cases=cases,
            approvals=approvals,
            auto_actions=auto_actions,
            exceptions=followups,
            settings=settings,
            store=store,
            risk=risk,
            playbooks=playbooks,
            chat=chats.for_agent("director"),
            language=settings.agents.language,
            langfuse=settings.langfuse,
        )
        mail = injector.get(MailClient) if settings.mail.configured else None  # type: ignore[type-abstract]
        return BriefingJob(
            builder,
            store,
            runtime=runtime,
            mail=mail,
            control_tower_url=settings.ui.public_url,
        )

    @provider
    @singleton
    def provide_assistant_store(self, db: Database) -> AssistantStore:  # type: ignore[type-abstract]
        return PostgresAssistantStore(db)

    @provider
    @singleton
    def provide_department_assistant(
        self,
        settings: Settings,
        chats: ChatClientFactory,
        cases: CaseStore,  # type: ignore[type-abstract]
        approvals: ApprovalsGateway,  # type: ignore[type-abstract]
        followups: FollowUpJob,
        auto_actions: AutoActionsStore,  # type: ignore[type-abstract]
        risk: RiskSource,  # type: ignore[type-abstract]
        playbooks: PlaybookEngine,
        orders: BoardOrders,  # type: ignore[type-abstract]
        performance: PerformanceSource,  # type: ignore[type-abstract]
        planning: PlanningReadStore,  # type: ignore[type-abstract]
        runtime: RuntimeSettingsReader,
    ) -> DepartmentAssistant:
        return DepartmentAssistant(
            chats.for_agent("director"),
            cases=cases,
            approvals=approvals,
            exceptions=followups,
            auto_actions=auto_actions,
            risk=risk,
            playbooks=playbooks,
            orders=orders,
            performance=performance,
            planning=planning,
            runtime=runtime,
            language=settings.agents.language,
            langfuse=settings.langfuse,
        )

    @provider
    @singleton
    def provide_plan_runner(
        self,
        cases: CaseStore,  # type: ignore[type-abstract]
        actions: ChatActions,
        sourcing: SourcingDispatcher,
        playbooks: PlaybookEngine,
    ) -> PlanRunner:
        return PlanRunner(cases=cases, actions=actions, sourcing=sourcing, playbooks=playbooks)

    @provider
    @singleton
    def provide_jobs(
        self,
        db: Database,
        cases: CaseStore,  # type: ignore[type-abstract]
        agents: Agents,
        escalator: Escalator,  # type: ignore[type-abstract]
        followups: FollowUpJob,
        recorder: FeedbackRecorder,
        feedback: FeedbackStore,  # type: ignore[type-abstract]
        suggestions: SuggestionStore,  # type: ignore[type-abstract]
        runtime: RuntimeSettingsReader,
        playbooks: PlaybookEngine,
        sourcing_source: SourcingSource,  # type: ignore[type-abstract]
        sourcing_dispatcher: SourcingDispatcher,
        briefing: BriefingJob,
    ) -> JobRunner:  # type: ignore[type-abstract]
        followups.attach_playbooks(playbooks)
        return JobDispatcher(
            {
                "briefing": briefing,
                "playbooks": PlaybooksJob(playbooks),
                "calibration": CalibrationJob(
                    recorder=recorder,
                    feedback=feedback,
                    suggestions=suggestions,
                    policy=policy_from(runtime),
                ),
                "po_followups": followups,
                "inventory_planning": PlanningJob(
                    cases=cases,
                    agents=agents,
                    escalator=escalator,
                    conversations=PostgresConversationLookup(db),
                ),
                "supplier_performance": PerformanceJob(
                    cases=cases,
                    agents=agents,
                    escalator=escalator,
                    conversations=PostgresConversationLookup(db),
                ),
                "sourcing_rounds": SourcingJob(sourcing_source, sourcing_dispatcher),
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
    def provide_performance_source(self, settings: Settings) -> PerformanceSource:  # type: ignore[type-abstract]
        return HttpPerformanceSource(settings.a2a.supplier_performance_url, settings.a2a_token)

    @provider
    @singleton
    def provide_risk_source(self, settings: Settings) -> RiskSource:  # type: ignore[type-abstract]
        return HttpRiskSource(settings.a2a.inventory_planning_url, settings.a2a_token)

    @provider
    @singleton
    def provide_calendar(self, db: Database) -> CalendarStore:  # type: ignore[type-abstract]
        return PostgresCalendarStore(db)

    @provider
    @singleton
    def provide_sourcing_source(self, settings: Settings) -> SourcingSource:  # type: ignore[type-abstract]
        return HttpSourcingSource(settings.a2a.sourcing_url, settings.a2a_token)

    @provider
    @singleton
    def provide_sourcing_dispatcher(
        self,
        cases: CaseStore,  # type: ignore[type-abstract]
        agents: Agents,
        escalator: Escalator,  # type: ignore[type-abstract]
        db: Database,
    ) -> SourcingDispatcher:
        return SourcingDispatcher(
            cases=cases,
            agents=agents,
            escalator=escalator,
            conversations=PostgresConversationLookup(db),
        )

    @provider
    @singleton
    def provide_mailbox_sync(self, settings: Settings) -> MailboxSync:  # type: ignore[type-abstract]
        return HttpMailboxSync(
            settings.mail_sync.url, HmacSigner(settings.events.signing_secret.get_secret_value())
        )

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
    def provide_auto_actions(self, db: Database) -> AutoActionsStore:  # type: ignore[type-abstract]
        return PostgresAutoActionsStore(db)

    @provider
    @singleton
    def provide_reverter(self, orders: PurchaseOrderRepo) -> Reverter:  # type: ignore[type-abstract]
        return OdooReverter(orders)

    @provider
    @singleton
    def provide_autonomy_changes(
        self,
        approvals: ApprovalsGateway,  # type: ignore[type-abstract]
        store: RuntimeSettingsStore,  # type: ignore[type-abstract]
        reader: RuntimeSettingsReader,
        realtime: Realtime,  # type: ignore[type-abstract]
    ) -> AutonomyChanges:
        return AutonomyChanges(
            approvals=approvals, settings_store=store, reader=reader, realtime=realtime
        )

    @provider
    @singleton
    def provide_feedback_store(self, db: Database) -> FeedbackStore:  # type: ignore[type-abstract]
        return PostgresFeedbackStore(db)

    @provider
    @singleton
    def provide_suggestions(self, db: Database) -> SuggestionStore:  # type: ignore[type-abstract]
        return PostgresSuggestionStore(db)

    @provider
    @singleton
    def provide_profiles(self, db: Database) -> ProfileStore:  # type: ignore[type-abstract]
        return PostgresProfileStore(db)

    @provider
    @singleton
    def provide_feedback_recorder(
        self,
        approvals: ApprovalsGateway,  # type: ignore[type-abstract]
        store: FeedbackStore,  # type: ignore[type-abstract]
    ) -> FeedbackRecorder:
        return FeedbackRecorder(approvals, store)

    @provider
    @singleton
    def provide_playbook_store(self, db: Database) -> PlaybookStore:  # type: ignore[type-abstract]
        return PostgresPlaybookStore(db)

    @provider
    @singleton
    def provide_playbooks(
        self,
        store: PlaybookStore,  # type: ignore[type-abstract]
        cases: CaseStore,  # type: ignore[type-abstract]
        agents: Agents,
        escalator: Escalator,  # type: ignore[type-abstract]
        followups: FollowUpJob,
        db: Database,
    ) -> PlaybookEngine:
        return PlaybookEngine(
            store=store,
            cases=cases,
            agents=agents,
            escalator=escalator,
            facts=followups,
            conversations=PostgresConversationLookup(db),
        )

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
        autonomy: AutonomyChanges,
        feedback: FeedbackRecorder,
        settings: Settings,
    ) -> Deps:
        webhook = settings.director.teams_webhook_url.get_secret_value()
        return Deps(
            cases=cases,
            agents=agents,
            escalator=escalator,
            jobs=jobs,
            conversations=PostgresConversationLookup(db),
            autonomy=autonomy,
            feedback=feedback,
            notifier=TeamsNotifier(webhook, control_tower_url=settings.ui.public_url)
            if webhook
            else None,
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
        playbooks: PlaybookEngine,
    ) -> Orchestrator:
        locks = PoLocks(
            RedisLock(redis),
            ttl_seconds=settings.director.lock_ttl_seconds,
            wait_seconds=settings.director.lock_wait_seconds,
        )
        return Orchestrator(
            deps,
            results,
            playbooks=playbooks,
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
        startup=[_open_db, _connect_odoo, _start_push_relay],
        shutdown=[_stop_push_relay, _close_odoo, _close_agents, _close_db],
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


_push_task: asyncio.Task[None] | None = None


async def _start_push_relay() -> None:
    """Approvals reach the phones that subscribed, when a VAPID key pair is configured."""
    global _push_task  # noqa: PLW0603 - one background task per process
    if not (settings.ui.vapid_public_key and settings.ui.vapid_private_key.get_secret_value()):
        logger.info("web push off: SC__UI__VAPID_PUBLIC_KEY / PRIVATE_KEY not set")
        return
    relay = app.state.injector.get(PushRelay)
    _push_task = asyncio.create_task(relay.run(), name="push-relay")
    logger.info("web push on: approvals are relayed to subscribed browsers")


async def _stop_push_relay() -> None:
    if _push_task is not None:
        _push_task.cancel()
        with suppress(asyncio.CancelledError):
            await _push_task


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
    for client in _demo_clients:
        await client.aclose()


_demo_clients: list[OdooClient] = []  # the demo's second Odoo login, closed with the app


if not settings.events.configured:
    logger.warning("SC__EVENTS__SIGNING_SECRET not set; /events will reject every request")

app = build_app()
