"""A2A proxies for the agents the director talks to.

Each proxy wraps an ``AgentCaller`` (the ``sc_core`` A2A client, or a fake in
tests) with a semaphore, so a burst of events cannot open more concurrent
runs on one agent than ``max_concurrent``. The transport already forwards
the bearer token and the trace/case metadata; the proxy adds nothing to the
wire format, which keeps the agents plain A2A servers.

Phase 7 adds ``inventory_planning`` and phase 9 ``logistics``; until then a
task for an unknown agent is a routing bug and fails loudly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sc_core.a2a.client import A2AClient, AgentCaller
from sc_core.a2a.protocol import AgentReply
from sc_core.infra.settings import Settings

DEFAULT_MAX_CONCURRENT = 4


class AgentProxy:
    def __init__(
        self, name: str, caller: AgentCaller, *, max_concurrent: int = DEFAULT_MAX_CONCURRENT
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self.name = name
        self._caller = caller
        self._slots = asyncio.Semaphore(max_concurrent)
        self.in_flight = 0
        self.max_in_flight = 0

    async def send(self, task_json: str, *, case_id: str) -> AgentReply:
        async with self._slots:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                return await self._caller.send(task_json, case_id=case_id)
            finally:
                self.in_flight -= 1

    async def aclose(self) -> None:
        close = getattr(self._caller, "aclose", None)
        if close is not None:
            await close()


@dataclass
class Agents:
    """The agents by name; ``for_name`` is what the workflow proxies use."""

    supplier_comms: AgentProxy
    others: dict[str, AgentProxy] = field(default_factory=dict)

    def for_name(self, name: str) -> AgentProxy:
        if name == "supplier_comms":
            return self.supplier_comms
        try:
            return self.others[name]
        except KeyError:
            raise LookupError(f"no agent named {name!r} is configured") from None

    async def aclose(self) -> None:
        for proxy in (self.supplier_comms, *self.others.values()):
            await proxy.aclose()


def build_agents(settings: Settings) -> Agents:
    """Proxies over real A2A clients, from the ``SC__A2A__*`` urls."""
    supplier_comms = A2AClient(
        settings.a2a.supplier_comms_url,
        token=settings.a2a_token,
        timeout_seconds=settings.a2a.timeout_seconds,
    )
    inventory_planning = A2AClient(
        settings.a2a.inventory_planning_url,
        token=settings.a2a_token,
        timeout_seconds=max(settings.a2a.timeout_seconds, 900.0),  # a full plan takes minutes
    )
    return Agents(
        supplier_comms=AgentProxy(
            "supplier_comms", supplier_comms, max_concurrent=settings.a2a.max_concurrent
        ),
        others={
            "inventory_planning": AgentProxy(
                "inventory_planning", inventory_planning, max_concurrent=1
            )
        },
    )
