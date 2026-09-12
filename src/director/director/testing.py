"""Injector module that runs the director on in-memory doubles (tests, e2e harnesses)."""

from __future__ import annotations

from injector import Binder, Module, singleton

from director.dispatch import Dispatcher, EventResults, MemoryEventResults
from director.inbox import EventInbox, MemoryEventInbox
from sc_core.a2a.client import AgentCaller
from sc_core.a2a.testing import FakeAgentCaller


class MemoryDirectorModule(Module):
    def __init__(
        self,
        inbox: MemoryEventInbox | None = None,
        results: MemoryEventResults | None = None,
        supplier_comms: AgentCaller | None = None,
    ) -> None:
        self.inbox = inbox or MemoryEventInbox()
        self.results = results or MemoryEventResults()
        self.supplier_comms = supplier_comms or FakeAgentCaller()

    def configure(self, binder: Binder) -> None:
        binder.bind(EventInbox, to=self.inbox, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(EventResults, to=self.results, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(Dispatcher, to=Dispatcher(self.supplier_comms, self.results), scope=singleton)
