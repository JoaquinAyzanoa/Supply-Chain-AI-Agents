"""Injector module that runs the director on in-memory doubles (tests, e2e harnesses)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from injector import Binder, Module, singleton

from director.agents import AgentProxy, Agents
from director.api.approvals import ApprovalsGateway
from director.api.auth import LoginRateLimit, MemoryUserStore, UserStore
from director.api.board import BoardMoves, BoardOrders
from director.api.chat import CaseAssistant, ChatActions, EmailSnapshot
from director.api.exceptions import ExceptionsSource
from director.api.mailbox import MailboxSync
from director.api.planning import DemandSource, PlanningLineRow, PlanningReadStore, PlanningRunRow
from director.api.runs import RunsGateway, SchedulerRuns
from director.api.settings import MemoryRuntimeSettingsStore, RuntimeSettingsStore
from director.concurrency import PoLocks
from director.conversations import MemoryConversationLookup, MemoryMailActivity
from director.escalation import Escalator, MemoryEscalator
from director.handlers.followups import MailActivity
from director.inbox import EventInbox, EventResults, MemoryEventInbox, MemoryEventResults
from director.jobs import JobRunner, NoJobs
from director.policies import FollowUpPolicy, PoFacts
from director.realtime import BroadcastingCaseStore
from director.store import CaseStore, MemoryCaseStore
from director.workflow import ConfirmedOrders, Deps, Orchestrator
from sc_core.a2a.client import AgentCaller
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.app.realtime import MemoryRealtime, Realtime
from sc_core.infra.locks import MemoryLock
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.client import ChatCompleter
from sc_core.llm.testing import ScriptedChatClient
from sc_core.odoo.models import AgentRun, Approval, ApprovalStatus, PurchaseOrder, Ref
from sc_core.shared.errors import ScError


def memory_deps(
    *,
    cases: CaseStore | None = None,
    supplier_comms: AgentCaller | None = None,
    inventory_planning: AgentCaller | None = None,
    escalator: Escalator | None = None,
    jobs: JobRunner | None = None,
    conversations: MemoryConversationLookup | None = None,
    max_concurrent: int = 4,
) -> Deps:
    others = {}
    if inventory_planning is not None:
        others["inventory_planning"] = AgentProxy(
            "inventory_planning", inventory_planning, max_concurrent=max_concurrent
        )
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
    )


class MemoryDirectorModule(Module):
    def __init__(
        self,
        inbox: MemoryEventInbox | None = None,
        results: MemoryEventResults | None = None,
        supplier_comms: AgentCaller | None = None,
        *,
        inventory_planning: AgentCaller | None = None,
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
        self.deps = memory_deps(
            cases=self.case_store,
            supplier_comms=supplier_comms,
            inventory_planning=inventory_planning,
            escalator=self.escalator,
            jobs=jobs,
        )
        self.orchestrator = Orchestrator(
            self.deps,
            self.results,
            inbox=self.inbox,
            locks=PoLocks(self.lock, wait_seconds=lock_wait_seconds, poll_seconds=0.01),
            orders=orders,
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
        binder.bind(ChatActions, to=ChatActions(self.deps, self.approvals), scope=singleton)
        binder.bind(MailActivity, to=self.mail_activity, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(BoardOrders, to=self.board_orders, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(MailboxSync, to=self.mailbox, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(BoardMoves, to=BoardMoves(self.board_orders, self.deps), scope=singleton)
        binder.bind(Orchestrator, to=self.orchestrator, scope=singleton)
        binder.bind(UserStore, to=self.users, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(ApprovalsGateway, to=self.approvals, scope=singleton)  # type: ignore[type-abstract]
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
                requested_by="supplier_comms",
                case_id=thread_id,
                thread_id=thread_id,
                callback_status="none",
            )
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
        self.actions: list[tuple[str, int, Any]] = []
        self.notes: list[tuple[int, str]] = []

    def add(self, po: PurchaseOrder) -> PurchaseOrder:
        self.orders[po.id] = po
        return po

    async def board_orders(self, *, closed_since: date) -> list[PurchaseOrder]:
        return list(self.orders.values())

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
