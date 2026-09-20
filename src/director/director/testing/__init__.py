"""Injector module that runs the director on in-memory doubles (tests, e2e harnesses)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from injector import Binder, Module, singleton

from director.api.approvals import ApprovalsGateway
from director.api.auth import LoginRateLimit, MemoryUserStore, UserStore
from director.api.board import BoardMoves, BoardOrders
from director.api.chat import CaseAssistant, ChatActions, EmailSnapshot
from director.api.demo import DirectorApprovalResolver
from director.api.exceptions import ExceptionsSource
from director.api.mailbox import MailboxSync
from director.api.performance import PerformanceSource
from director.api.planning import DemandSource, PlanningLineRow, PlanningReadStore, PlanningRunRow
from director.api.risk import RiskSource
from director.api.runs import RunsGateway, SchedulerRuns
from director.api.settings import MemoryRuntimeSettingsStore, RuntimeSettingsStore
from director.api.suppliers import MailLinks, SupplierPrices
from director.desk.assistant import (
    AssistantStore,
    DepartmentAssistant,
    MemoryAssistantStore,
    PlanRunner,
)
from director.desk.autonomy import AutoAction, AutoActionsStore, AutonomyChanges, Reverter
from director.desk.briefing import BriefingBuilder, BriefingJob, BriefingStore, MemoryBriefingStore
from director.desk.demo import (
    DemoDirector,
    DemoStore,
    DemoWorld,
    MemoryDemoStore,
    MemoryDemoWorld,
    MemorySupplierMailbox,
    SupplierMailbox,
)
from director.desk.learning import (
    FeedbackRecorder,
    FeedbackStore,
    MemoryFeedbackStore,
    MemorySuggestionStore,
    SuggestionStore,
)
from director.desk.sourcing import SourcingDispatcher, SourcingSource
from director.handlers.followups import MailActivity
from director.notifications.push import MemoryPushStore, PushStore
from director.notifications.realtime import BroadcastingCaseStore
from director.orchestration.agents import AgentProxy, Agents
from director.orchestration.concurrency import PoLocks
from director.orchestration.conversations import MemoryConversationLookup, MemoryMailActivity
from director.orchestration.escalation import Escalator, MemoryEscalator
from director.orchestration.inbox import (
    EventInbox,
    EventResults,
    MemoryEventInbox,
    MemoryEventResults,
)
from director.orchestration.jobs import JobRunner, NoJobs
from director.orchestration.policies import FollowUpPolicy, PoFacts
from director.orchestration.store import CaseStore, MemoryCaseStore
from director.orchestration.workflow import ConfirmedOrders, Deps, Orchestrator
from director.playbooks import MemoryPlaybookStore, PlaybookEngine, PlaybookStore
from sc_core.a2a.client import AgentCaller
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app.realtime import MemoryRealtime, Realtime
from sc_core.infra.calendar import CalendarStore, MemoryCalendarStore
from sc_core.infra.locks import MemoryLock
from sc_core.infra.profiles import MemoryProfileStore, ProfileStore
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import LangfuseCfg, Settings
from sc_core.llm.client import ChatCompleter
from sc_core.llm.testing import ScriptedChatClient
from sc_core.odoo.models import (
    AgentRun,
    Approval,
    ApprovalStatus,
    MailLink,
    PurchaseOrder,
    PurchaseOrderLine,
    Ref,
    SupplierInfo,
)
from sc_core.schema.runtime_settings import RuntimeSettings
from sc_core.shared.errors import ScError, ValidationFailed


def memory_deps(
    *,
    cases: CaseStore | None = None,
    supplier_comms: AgentCaller | None = None,
    inventory_planning: AgentCaller | None = None,
    logistics: AgentCaller | None = None,
    invoice_match: AgentCaller | None = None,
    supplier_performance: AgentCaller | None = None,
    sourcing: AgentCaller | None = None,
    escalator: Escalator | None = None,
    jobs: JobRunner | None = None,
    conversations: MemoryConversationLookup | None = None,
    max_concurrent: int = 4,
    autonomy: AutonomyChanges | None = None,
    feedback: FeedbackRecorder | None = None,
) -> Deps:
    others = {}
    if inventory_planning is not None:
        others["inventory_planning"] = AgentProxy(
            "inventory_planning", inventory_planning, max_concurrent=max_concurrent
        )
    if logistics is not None:
        others["logistics"] = AgentProxy("logistics", logistics, max_concurrent=max_concurrent)
    if invoice_match is not None:
        others["invoice_match"] = AgentProxy(
            "invoice_match", invoice_match, max_concurrent=max_concurrent
        )
    if supplier_performance is not None:
        others["supplier_performance"] = AgentProxy(
            "supplier_performance", supplier_performance, max_concurrent=max_concurrent
        )
    if sourcing is not None:
        others["sourcing"] = AgentProxy("sourcing", sourcing, max_concurrent=max_concurrent)
    return Deps(
        cases=cases or MemoryCaseStore(),
        agents=Agents(
            supplier_comms=AgentProxy(
                "supplier_comms", supplier_comms or FakeAgentCaller(), max_concurrent=max_concurrent
            ),
            others=others,
        ),
        escalator=escalator or MemoryEscalator(),
        jobs=jobs or NoJobs(),
        conversations=conversations or MemoryConversationLookup(),
        autonomy=autonomy,
        feedback=feedback,
    )


class MemoryDirectorModule(Module):
    def __init__(
        self,
        inbox: MemoryEventInbox | None = None,
        results: MemoryEventResults | None = None,
        supplier_comms: AgentCaller | None = None,
        *,
        inventory_planning: AgentCaller | None = None,
        logistics: AgentCaller | None = None,
        invoice_match: AgentCaller | None = None,
        supplier_performance: AgentCaller | None = None,
        sourcing: AgentCaller | None = None,
        cases: MemoryCaseStore | None = None,
        escalator: Escalator | None = None,
        jobs: JobRunner | None = None,
        orders: ConfirmedOrders | None = None,
        lock_wait_seconds: float = 2.0,
        chat: ChatCompleter | None = None,
    ) -> None:
        self.inbox = inbox or MemoryEventInbox()
        self.results = results or MemoryEventResults(self.inbox)
        self.results.attach(self.inbox)
        self.realtime = MemoryRealtime()
        self.cases = cases or MemoryCaseStore()
        self.case_store = BroadcastingCaseStore(self.cases, self.realtime)
        self.escalator = escalator or MemoryEscalator()
        self.lock = MemoryLock()
        self.users = MemoryUserStore()
        self.approvals = MemoryApprovalsGateway()
        self.runtime_settings = MemoryRuntimeSettingsStore()
        self.runtime_reader = RuntimeSettingsReader(None, defaults=RuntimeSettings())
        self.auto_actions = MemoryAutoActionsStore()
        self.reverter = MemoryReverter()
        self.feedback = MemoryFeedbackStore()
        self.suggestions = MemorySuggestionStore()
        self.profiles = MemoryProfileStore()
        self.recorder = FeedbackRecorder(self.approvals, self.feedback)
        self.playbook_store = MemoryPlaybookStore()
        self.autonomy = AutonomyChanges(
            approvals=self.approvals,
            settings_store=self.runtime_settings,
            reader=self.runtime_reader,
            realtime=self.realtime,
        )
        self.login_limit = LoginRateLimit(per_minute=5)
        self.runs = MemoryRunsGateway()
        self.scheduler_runs = MemorySchedulerRuns()
        self.planning = MemoryPlanningReadStore()
        self.demand = MemoryDemandSource()
        self.exceptions = MemoryExceptionsSource()
        self.chat = chat or ScriptedChatClient()
        self.orders_lookup = MemoryOrderLookup()
        self.emails = MemoryEmailReader()
        self.mail_activity = MemoryMailActivity()
        self.board_orders = MemoryBoardOrders()
        self.mailbox = MemoryMailboxSync()
        self.performance = MemoryPerformanceSource()
        self.sourcing_source = MemorySourcingSource()
        self.risk_source = MemoryRiskSource()
        self.calendar = MemoryCalendarStore()
        # the briefing links records to Odoo; the test settings name a fake Odoo
        self.settings = Settings(
            _env_file=None,
            service_name="director",
            environment="test",
            odoo={"url": "http://odoo.test:8069"},
        )
        self.briefings = MemoryBriefingStore()
        self.push_store = MemoryPushStore()
        self.prices = MemorySupplierPrices()
        self.mail_links = MemoryMailLinks()
        self.assistant_store = MemoryAssistantStore()
        self.demo_store = MemoryDemoStore()
        self.demo_world = MemoryDemoWorld()
        self.supplier_mailbox = MemorySupplierMailbox()
        self.sent_mail: list[Any] = []
        self.deps = memory_deps(
            cases=self.case_store,
            supplier_comms=supplier_comms,
            inventory_planning=inventory_planning,
            logistics=logistics,
            invoice_match=invoice_match,
            supplier_performance=supplier_performance,
            sourcing=sourcing,
            escalator=self.escalator,
            autonomy=self.autonomy,
            feedback=self.recorder,
            jobs=jobs,
        )
        self.sourcing = SourcingDispatcher(
            cases=self.case_store, agents=self.deps.agents, escalator=self.escalator
        )
        self.playbooks = PlaybookEngine(
            store=self.playbook_store,
            cases=self.case_store,
            agents=self.deps.agents,
            escalator=self.escalator,
            facts=self.exceptions,
        )
        self.orchestrator = Orchestrator(
            self.deps,
            self.results,
            inbox=self.inbox,
            playbooks=self.playbooks,
            locks=PoLocks(self.lock, wait_seconds=lock_wait_seconds, poll_seconds=0.01),
            orders=orders,
        )

    def _briefing_job(self) -> BriefingJob:
        if getattr(self, "_briefing", None) is None:
            self._briefing = BriefingJob(
                BriefingBuilder(
                    cases=self.case_store,
                    approvals=self.approvals,
                    auto_actions=self.auto_actions,
                    exceptions=self.exceptions,
                    settings=self.settings,
                    store=self.briefings,
                    risk=self.risk_source,
                    playbooks=self.playbooks,
                    chat=self.chat,
                    langfuse=LangfuseCfg(enabled=False),
                ),
                self.briefings,
                runtime=self.runtime_reader,
                mail=_RecordingMail(self.sent_mail),
                control_tower_url="https://tower.test",
            )
        return self._briefing

    def demo_director(self, *, briefing: BriefingJob | None = None) -> DemoDirector:
        """The scripted scenario on the memory doubles: no waiting, three looks per step."""

        async def no_sleep(_: float) -> None:
            return None

        return DemoDirector(
            cfg=self.settings.demo,
            store=self.demo_store,
            world=self.demo_world,
            mailbox=self.supplier_mailbox,
            deps=self.deps,
            approvals=self.approvals,
            cases=self.case_store,
            mailbox_sync=self.mailbox,
            risk=self.risk_source,
            sourcing=self.sourcing,
            sourcing_source=self.sourcing_source,
            briefing=briefing or self._briefing_job(),
            resolver=DirectorApprovalResolver(
                self.approvals, self.case_store, self.autonomy, self.recorder
            ),
            sleep=no_sleep,
            wait_seconds=3,
            poll_seconds=1.0,
            chain_wait_seconds=1,
        )

    def configure(self, binder: Binder) -> None:
        binder.bind(EventInbox, to=self.inbox, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(EventResults, to=self.results, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(CaseStore, to=self.case_store, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(Realtime, to=self.realtime, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(RunsGateway, to=self.runs, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(SchedulerRuns, to=self.scheduler_runs, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(PlanningReadStore, to=self.planning, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(DemandSource, to=self.demand, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(ExceptionsSource, to=self.exceptions, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(Agents, to=self.deps.agents, scope=singleton)
        binder.bind(Deps, to=self.deps, scope=singleton)
        binder.bind(
            CaseAssistant,
            to=CaseAssistant(
                self.chat,
                cases=self.case_store,
                approvals=self.approvals,
                orders=self.orders_lookup,
                policy=self.exceptions,
                emails=self.emails,
                langfuse=LangfuseCfg(enabled=False),
            ),
            scope=singleton,
        )
        actions = ChatActions(self.deps, self.approvals)
        binder.bind(ChatActions, to=actions, scope=singleton)
        binder.bind(BriefingStore, to=self.briefings, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(PushStore, to=self.push_store, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(SupplierPrices, to=self.prices, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(MailLinks, to=self.mail_links, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(BriefingJob, to=self._briefing_job(), scope=singleton)
        binder.bind(AssistantStore, to=self.assistant_store, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(DemoStore, to=self.demo_store, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(DemoWorld, to=self.demo_world, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(SupplierMailbox, to=self.supplier_mailbox, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(DemoDirector, to=self.demo_director(), scope=singleton)
        binder.bind(
            DepartmentAssistant,
            to=DepartmentAssistant(
                self.chat,
                cases=self.case_store,
                approvals=self.approvals,
                exceptions=self.exceptions,
                auto_actions=self.auto_actions,
                risk=self.risk_source,
                playbooks=self.playbooks,
                orders=self.board_orders,
                performance=self.performance,
                planning=self.planning,
                runtime=self.runtime_reader,
                langfuse=LangfuseCfg(enabled=False),
            ),
            scope=singleton,
        )
        binder.bind(
            PlanRunner,
            to=PlanRunner(
                cases=self.case_store,
                actions=actions,
                sourcing=self.sourcing,
                playbooks=self.playbooks,
            ),
            scope=singleton,
        )
        binder.bind(MailActivity, to=self.mail_activity, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(BoardOrders, to=self.board_orders, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(MailboxSync, to=self.mailbox, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(PerformanceSource, to=self.performance, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(BoardMoves, to=BoardMoves(self.board_orders, self.deps), scope=singleton)
        binder.bind(Orchestrator, to=self.orchestrator, scope=singleton)
        binder.bind(UserStore, to=self.users, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(ApprovalsGateway, to=self.approvals, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(AutoActionsStore, to=self.auto_actions, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(Reverter, to=self.reverter, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(AutonomyChanges, to=self.autonomy, scope=singleton)
        binder.bind(FeedbackStore, to=self.feedback, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(SuggestionStore, to=self.suggestions, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(ProfileStore, to=self.profiles, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(FeedbackRecorder, to=self.recorder, scope=singleton)
        binder.bind(PlaybookStore, to=self.playbook_store, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(PlaybookEngine, to=self.playbooks, scope=singleton)
        binder.bind(SourcingSource, to=self.sourcing_source, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(RiskSource, to=self.risk_source, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(CalendarStore, to=self.calendar, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(SourcingDispatcher, to=self.sourcing, scope=singleton)
        binder.bind(RuntimeSettingsReader, to=self.runtime_reader, scope=singleton)
        binder.bind(RuntimeSettingsStore, to=self.runtime_settings, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(LoginRateLimit, to=self.login_limit, scope=singleton)


class MemoryApprovalsGateway:
    """Approvals as Odoo would return them; ``resolve`` records the decision and fires nothing."""

    def __init__(self) -> None:
        self.rows: dict[int, Approval] = {}
        self.resolved: list[dict[str, Any]] = []

    def add(self, approval: Approval) -> Approval:
        self.rows[approval.id] = approval
        return approval

    def seed(
        self,
        approval_id: int,
        *,
        kind: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        po: tuple[int, str] | None = (15, "P00015"),
        thread_id: str | None = "case_msg1",
        status: ApprovalStatus = "pending",
        requested_by: str = "supplier_comms",
    ) -> Approval:
        import json as _json

        return self.add(
            Approval(
                id=approval_id,
                kind=kind,  # type: ignore[arg-type]
                summary=summary,
                status=status,
                po_id=Ref(id=po[0], name=po[1]) if po else None,
                payload_json=_json.dumps(payload or {}),
                requested_by=requested_by,
                case_id=thread_id,
                thread_id=thread_id,
                callback_status="none",
            )
        )

    async def create(
        self,
        *,
        kind: str,
        summary: str,
        payload: dict[str, Any],
        requested_by: str,
        case_id: str,
        po_id: int | None = None,
    ) -> Approval:
        approval_id = max(self.rows, default=100) + 1
        return self.seed(
            approval_id,
            kind=kind,
            summary=summary,
            payload=payload,
            po=None if po_id is None else (po_id, f"P{po_id:05d}"),
            thread_id=case_id,
            requested_by=requested_by,
        )

    async def list(self, *, status: str, kind: str | None, po_name: str | None) -> list[Approval]:
        return [
            a
            for a in self.rows.values()
            if (status == "all" or a.status == status)
            and (kind is None or a.kind == kind)
            and (po_name is None or (a.po_id is not None and a.po_id.name == po_name))
        ]

    async def get(self, approval_id: int) -> Approval:
        from sc_core.shared.errors import NotFound

        if approval_id not in self.rows:
            raise NotFound(f"sc.approval {approval_id} not found")
        return self.rows[approval_id]

    async def resolve(
        self,
        approval_id: int,
        status: ApprovalStatus,
        *,
        by_name: str,
        reason: str | None,
        details: dict[str, Any] | None,
    ) -> Approval:
        self.resolved.append(
            {
                "id": approval_id,
                "status": status,
                "by_name": by_name,
                "reason": reason,
                "details": details,
            }
        )
        updated = self.rows[approval_id].model_copy(
            update={
                "status": status,
                "resolved_by_name": by_name,
                "resolved_via": "api",
                "reason": reason,
                "callback_status": "sent",
            }
        )
        self.rows[approval_id] = updated
        return updated


class MemoryRunsGateway:
    def __init__(self) -> None:
        self.rows: list[AgentRun] = []

    async def recent(
        self,
        *,
        agent: str | None = None,
        model: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        found = [
            r
            for r in self.rows
            if (agent is None or r.agent == agent)
            and (model is None or r.model == model)
            and (status is None or r.status == status)
            and (since is None or (r.started_at is not None and r.started_at >= since))
        ]
        return sorted(found, key=lambda r: r.started_at or datetime.min, reverse=True)[:limit]

    async def for_cases(self, case_ids: Sequence[str]) -> list[AgentRun]:
        return [r for r in self.rows if r.case_id in set(case_ids)]


class MemorySchedulerRuns:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def recent(self, *, job_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        found = [r for r in self.rows if job_id is None or r["job_id"] == job_id]
        return sorted(found, key=lambda r: r["started_at"], reverse=True)[:limit]


class MemoryPlanningReadStore:
    def __init__(self) -> None:
        self.rows: dict[str, PlanningRunRow] = {}
        self.line_rows: dict[str, list[PlanningLineRow]] = {}

    async def runs(self, *, limit: int = 30) -> list[PlanningRunRow]:
        return sorted(self.rows.values(), key=lambda r: r.created_at, reverse=True)[:limit]

    async def run(self, run_id: str) -> PlanningRunRow | None:
        return self.rows.get(run_id)

    async def lines(self, run_id: str) -> list[PlanningLineRow]:
        return list(self.line_rows.get(run_id, []))


class MemoryExceptionsSource:
    """Facts the follow-up job would gather, and a record of "act now" requests."""

    def __init__(self, facts: list[PoFacts] | None = None) -> None:
        self.facts: list[PoFacts] = facts or []
        self.policy = FollowUpPolicy()
        self.acted: list[dict[str, Any]] = []

    async def effective_policy(self) -> FollowUpPolicy:
        return self.policy

    async def gather(self, today: date) -> list[PoFacts]:
        return list(self.facts)

    async def facts_for(self, po_name: str, today: date) -> PoFacts | None:
        return next((f for f in self.facts if f.po_name == po_name), None)

    async def act_now(
        self, po_name: str, *, requested_by: str, today: date | None = None
    ) -> dict[str, Any]:
        self.acted.append({"po_name": po_name, "requested_by": requested_by})
        return {"po_name": po_name, "status": "sent"}


class MemoryDemandSource:
    def __init__(self) -> None:
        self.histories: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[int, int]] = []

    async def history(
        self, product_id: int, *, days: int, warehouse_code: str | None
    ) -> dict[str, Any]:
        self.calls.append((product_id, days))
        return self.histories.get(product_id) or {"days": []}


class MemoryOrderLookup:
    def __init__(self) -> None:
        self.orders: dict[str, Any] = {}

    async def by_names(self, names: Sequence[str]) -> list[Any]:
        return [self.orders[n] for n in names if n in self.orders]


class MemoryEmailReader:
    def __init__(self) -> None:
        self.messages: dict[str, EmailSnapshot] = {}
        self.reads: list[str] = []

    async def read(self, message_id: str) -> EmailSnapshot | None:
        self.reads.append(message_id)
        return self.messages.get(message_id)


class MemoryBoardOrders:
    """Orders as Odoo would list them; moves are recorded and applied to the rows."""

    def __init__(self) -> None:
        self.orders: dict[int, PurchaseOrder] = {}
        self.off_board: set[int] = set()  # too old for the board's window
        self.lines_by_po: dict[int, list[PurchaseOrderLine]] = {}
        self.actions: list[tuple[str, int, Any]] = []
        self.notes: list[tuple[int, str]] = []

    def add(self, po: PurchaseOrder) -> PurchaseOrder:
        self.orders[po.id] = po
        return po

    async def board_orders(self, *, closed_since: date) -> list[PurchaseOrder]:
        return [po for po in self.orders.values() if po.id not in self.off_board]

    async def by_names(self, names: list[str]) -> list[PurchaseOrder]:
        return [po for po in self.orders.values() if po.name in names]

    async def lines(self, po_id: int) -> list[PurchaseOrderLine]:
        return list(self.lines_by_po.get(po_id, []))

    async def confirm(self, po_id: int) -> PurchaseOrder:
        self.actions.append(("confirm", po_id, None))
        self.orders[po_id] = self.orders[po_id].model_copy(update={"state": "purchase"})
        return self.orders[po_id]

    async def cancel(self, po_id: int) -> None:
        self.actions.append(("cancel", po_id, None))
        self.orders[po_id] = self.orders[po_id].model_copy(update={"state": "cancel"})

    async def mark_done(self, po_id: int) -> None:
        self.actions.append(("done", po_id, None))
        self.orders[po_id] = self.orders[po_id].model_copy(update={"state": "done"})

    async def set_supplier_confirmed(self, po_id: int, value: bool) -> None:
        self.actions.append(("supplier_confirmed", po_id, value))
        self.orders[po_id] = self.orders[po_id].model_copy(update={"sc_supplier_confirmed": value})

    async def post_note(self, po_id: int, body_html: str) -> int:
        self.notes.append((po_id, body_html))
        return len(self.notes)


class MemoryMailboxSync:
    """Answers a canned sync report; ``fail`` makes the service unreachable."""

    def __init__(self) -> None:
        self.report: dict[str, Any] = {"status": "ok", "fetched": 0}
        self.calls: list[str] = []
        self.fail = False

    async def run(self, *, requested_by: str) -> dict[str, Any]:
        self.calls.append(requested_by)
        if self.fail:
            raise ScError("the mailbox service did not answer")
        return dict(self.report)


class MemoryPerformanceSource:
    """Scores and rankings as the performance agent would answer them."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.rankings: dict[int, dict[str, Any]] = {}

    async def scores(self) -> list[dict[str, Any]]:
        return list(self.rows)

    async def rank(self, product_id: int) -> dict[str, Any]:
        return self.rankings.get(product_id) or {"product_id": product_id, "suppliers": []}

    async def rank_many(self, product_ids: list[int]) -> list[dict[str, Any]]:
        return [await self.rank(pid) for pid in sorted(set(product_ids))]


class MemorySupplierPrices:
    """Price list rows per supplier, as Odoo's ``product.supplierinfo`` would list them."""

    def __init__(self) -> None:
        self.rows: dict[int, list[SupplierInfo]] = {}
        self.variants: dict[int, int] = {}  # template id -> first variant id

    async def for_partner(self, partner_id: int) -> list[SupplierInfo]:
        return list(self.rows.get(partner_id, []))

    async def variant_ids(self, template_ids: list[int]) -> dict[int, int]:
        return {tid: self.variants[tid] for tid in template_ids if tid in self.variants}


class MemoryMailLinks:
    """Mail links per order id (metadata only)."""

    def __init__(self) -> None:
        self.rows: dict[int, list[MailLink]] = {}

    async def for_po(self, po_id: int) -> list[MailLink]:
        return list(self.rows.get(po_id, []))


class _RecordingMail:
    """Only ``send`` is used by the briefing; everything else is never called in tests."""

    def __init__(self, sent: list[Any]) -> None:
        self._sent = sent

    async def send(self, message: Any) -> None:
        self._sent.append(message)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


class MemoryRiskSource:
    """The risk radar as the planner would answer it."""

    def __init__(self) -> None:
        self.report_data: dict[str, Any] = {
            "as_of": "2026-09-14",
            "warehouse_code": "WH",
            "products": [],
            "suppliers": [],
            "cash_exposure": 0.0,
            "at_risk_30": 0,
        }

    async def report(self, *, warehouse_code: str | None = None) -> dict[str, Any]:
        return dict(self.report_data)


class MemorySourcingSource:
    """Rounds and negotiations as the sourcing agent would list them."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.due: list[dict[str, Any]] = []
        self.negotiation_rows: dict[str, list[dict[str, Any]]] = {}

    async def rounds(
        self, *, status: str | None = None, partner_id: int | None = None
    ) -> list[dict[str, Any]]:
        out = list(self.rows)
        if status:
            out = [r for r in out if r.get("status") == status or status == "active"]
        if partner_id is not None:
            out = [
                r for r in out if any(x.get("partner_id") == partner_id for x in r.get("rfqs", []))
            ]
        return out

    async def round(self, round_id: int) -> dict[str, Any] | None:
        return next((r for r in self.rows if r.get("id") == round_id), None)

    async def due_rounds(self) -> list[dict[str, Any]]:
        return list(self.due)

    async def negotiations(self, po_name: str) -> list[dict[str, Any]]:
        return list(self.negotiation_rows.get(po_name, []))


class MemoryAutoActionsStore:
    """The automatic-actions feed as the agents would have written it."""

    def __init__(self) -> None:
        self.rows: dict[int, AutoAction] = {}

    def add(self, action: AutoAction) -> AutoAction:
        self.rows[action.id] = action
        return action

    async def recent(self, *, since: datetime, limit: int = 200) -> list[AutoAction]:
        rows = [a for a in self.rows.values() if a.created_at >= since]
        return sorted(rows, key=lambda a: a.created_at, reverse=True)[:limit]

    async def for_case(self, case_id: str) -> list[AutoAction]:
        return await self.for_threads([case_id])

    async def for_threads(self, ids: Sequence[str]) -> list[AutoAction]:
        wanted = set(ids)
        return sorted(
            (a for a in self.rows.values() if a.case_id in wanted), key=lambda a: a.created_at
        )

    async def get(self, action_id: int) -> AutoAction | None:
        return self.rows.get(action_id)

    async def mark_reverted(self, action_id: int, *, by: str) -> AutoAction:
        if action_id not in self.rows:
            raise ScError(f"automatic action {action_id} not found")
        updated = self.rows[action_id].model_copy(
            update={"reverted_at": datetime.now(UTC), "reverted_by": by}
        )
        self.rows[action_id] = updated
        return updated


class MemoryReverter:
    def __init__(self) -> None:
        self.reverted: list[tuple[int, str]] = []

    async def revert(self, action: AutoAction, *, by: str) -> str:
        if action.kind != "po_change" or not action.revert:
            raise ValidationFailed(f"an automatic {action.kind} cannot be reverted")
        self.reverted.append((action.id, by))
        return f"Automatic change #{action.id} reverted by {by}"
