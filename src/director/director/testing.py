"""Injector module that runs the director on in-memory doubles (tests, e2e harnesses)."""

from __future__ import annotations

from injector import Binder, Module, singleton

from director.agents import AgentProxy, Agents
from director.conversations import MemoryConversationLookup
from director.escalation import Escalator, MemoryEscalator
from director.inbox import EventInbox, EventResults, MemoryEventInbox, MemoryEventResults
from director.jobs import JobRunner, NoJobs
from director.store import CaseStore, MemoryCaseStore
from director.workflow import Deps, Orchestrator
from sc_core.a2a.client import AgentCaller
from sc_core.a2a.testing import FakeAgentCaller


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
    ) -> None:
        self.inbox = inbox or MemoryEventInbox()
        self.results = results or MemoryEventResults()
        self.cases = cases or MemoryCaseStore()
        self.escalator = escalator or MemoryEscalator()
        self.deps = memory_deps(
            cases=self.cases, supplier_comms=supplier_comms, escalator=self.escalator, jobs=jobs
        )

    def configure(self, binder: Binder) -> None:
        binder.bind(EventInbox, to=self.inbox, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(EventResults, to=self.results, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(CaseStore, to=self.cases, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(Deps, to=self.deps, scope=singleton)
        binder.bind(Orchestrator, to=Orchestrator(self.deps, self.results), scope=singleton)
