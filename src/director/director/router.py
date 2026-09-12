"""Deterministic routing: event type → handler → what to do, with no model call.

Every event class in ``sc_core.schema.events`` has exactly one entry in
``ROUTES``; a test enumerates the union so a new event cannot be added
without a route. Handlers are pure functions: they look at the event and
return a ``Route`` that says which case the work belongs to, which agent
tasks to send, and what to record. The workflow (``workflow.py``) does the
I/O: cases, A2A calls, escalation.

Each task runs on its own agent thread: the event's ``case_id`` (unique per
inbound message or Odoo record change). The orchestrator's case groups those
threads; see ``store.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from director.store import CaseKind
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.base import StrictModel
from sc_core.schema.events import BaseEvent

AgentName = Literal["supplier_comms", "inventory_planning", "logistics"]

# Phase 7 adds the planning task, phase 9 the logistics task; a union keeps
# ``Route.dispatches`` typed without a wrapper per agent.
AgentTask = SupplierCommsTask


class Dispatch(StrictModel):
    """One task for one agent."""

    agent: AgentName
    task: AgentTask

    @property
    def thread_id(self) -> str:
        return self.task.case_id


class Route(StrictModel):
    """What the handler decided for an event."""

    case_kind: CaseKind
    po_name: str | None = None
    partner_id: int | None = None
    conversation_id: str | None = None
    dispatches: list[Dispatch] = []
    escalate: str | None = None
    """Reason to hand the event to a human right away (no agent can act)."""
    job: str | None = None
    """Scheduler job to run in-process (follow-ups, planning, performance)."""
    note: str | None = None
    """Something to record on the case when there is nothing to send."""


Handler = Callable[[BaseEvent], Route]


class UnroutableEvent(LookupError):
    def __init__(self, event: BaseEvent) -> None:
        super().__init__(f"no route for event type {event.type!r}")
        self.event = event


def _build_routes() -> dict[type[BaseEvent], Handler]:
    # Imported here so handlers can import ``Route`` from this module.
    from director.handlers import agent_events, inbound_mail, jobs, odoo_events, unlinked_mail
    from sc_core.schema import events as ev

    return {
        ev.InboundMailLinked: inbound_mail.handle,
        ev.InboundMailUnlinked: unlinked_mail.handle,
        ev.ScheduledTick: jobs.dispatch,
        ev.OdooPurchaseConfirmed: odoo_events.on_po_confirmed,
        ev.OdooReceiptValidated: odoo_events.on_receipt,
        ev.OdooOrderpointTriggered: odoo_events.on_orderpoint,
        ev.OdooApprovalResolved: odoo_events.on_approval_resolved,
        ev.AgentRunFinished: agent_events.on_run_finished,
    }


ROUTES: dict[type[BaseEvent], Handler] = _build_routes()


def route(event: BaseEvent) -> Route:
    """The routing decision for ``event``; ``UnroutableEvent`` for a type without a handler."""
    handler = ROUTES.get(type(event))
    if handler is None:
        raise UnroutableEvent(event)
    return handler(event)
