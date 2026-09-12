"""Injector module that runs the director on in-memory doubles (tests, e2e harnesses)."""

from __future__ import annotations

from injector import Binder, Module, singleton

from director.agents import AgentProxy, Agents
from director.api.auth import LoginRateLimit, MemoryUserStore, UserStore
from director.api.settings import MemoryRuntimeSettingsStore, RuntimeSettingsStore
from director.concurrency import PoLocks
from director.conversations import MemoryConversationLookup
from director.escalation import Escalator, MemoryEscalator
from director.inbox import EventInbox, EventResults, MemoryEventInbox, MemoryEventResults
from director.jobs import JobRunner, NoJobs
from director.store import CaseStore, MemoryCaseStore
from director.workflow import ConfirmedOrders, Deps, Orchestrator
from sc_core.a2a.client import AgentCaller
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.infra.locks import MemoryLock


def memory_deps(
    *,
    cases: MemoryCaseStore | None = None,
    supplier_comms: AgentCaller | None = None,
    escalator: Escalator | None = None,
    jobs: JobRunner | None = None,
    conversations: MemoryConversationLookup | None = None,
    max_concurrent: int = 4,
) -> Deps:
    return Deps(
        cases=cases or MemoryCaseStore(),
        agents=Agents(
            supplier_comms=AgentProxy(
                "supplier_comms", supplier_comms or FakeAgentCaller(), max_concurrent=max_concurrent
            )
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
        cases: MemoryCaseStore | None = None,
        escalator: Escalator | None = None,
        jobs: JobRunner | None = None,
        orders: ConfirmedOrders | None = None,
        lock_wait_seconds: float = 2.0,
    ) -> None:
        self.inbox = inbox or MemoryEventInbox()
        self.results = results or MemoryEventResults(self.inbox)
        self.results.attach(self.inbox)
        self.cases = cases or MemoryCaseStore()
        self.escalator = escalator or MemoryEscalator()
        self.lock = MemoryLock()
        self.users = MemoryUserStore()
        self.runtime_settings = MemoryRuntimeSettingsStore()
        self.login_limit = LoginRateLimit(per_minute=5)
        self.deps = memory_deps(
            cases=self.cases, supplier_comms=supplier_comms, escalator=self.escalator, jobs=jobs
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
        binder.bind(CaseStore, to=self.cases, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(Deps, to=self.deps, scope=singleton)
        binder.bind(Orchestrator, to=self.orchestrator, scope=singleton)
        binder.bind(UserStore, to=self.users, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(RuntimeSettingsStore, to=self.runtime_settings, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(LoginRateLimit, to=self.login_limit, scope=singleton)
