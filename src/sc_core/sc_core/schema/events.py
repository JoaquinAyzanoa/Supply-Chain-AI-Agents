"""Event contract.

Every event that moves between services (mail_sync → director, scheduler →
director, Odoo → director, agents → director) extends ``BaseEvent``. The base carries
what routing, tracing and idempotent handling need; subclasses add the
business payload and fix ``type`` to a literal so events can be parsed as a
discriminated union.

Rules for payloads: identifiers and small facts only. Never an email body,
never a secret. Consumers fetch details from the source system by id.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import AwareDatetime, Field, TypeAdapter

from sc_core.schema.base import StrictModel
from sc_core.shared.idempotency import deterministic_id, new_id
from sc_core.shared.time import utc_now


class BaseEvent(StrictModel):
    """Common envelope. Subclasses override ``type`` with a ``Literal``."""

    SCHEMA_VERSION: ClassVar[int] = 1

    type: str
    schema_version: int = Field(default=1, ge=1)
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    occurred_at: AwareDatetime = Field(default_factory=utc_now)
    source: str = Field(min_length=1, description="service that emitted the event")
    case_id: str = Field(min_length=1, description="business case this event belongs to")
    trace_id: str | None = Field(default=None, description="Langfuse trace to attach work to")

    def with_trace(self, trace_id: str) -> BaseEvent:
        """Copy with a trace id, for the emitter that opens the trace."""
        return self.model_copy(update={"trace_id": trace_id})


# --- phase 4: mail sync and scheduler -------------------------------------------

LinkConfidence = Literal["exact", "sender_single_open_po"]


class InboundMailLinked(BaseEvent):
    """A supplier message was attached to a purchase order by rule (no model call)."""

    type: Literal["inbound_mail.linked"] = "inbound_mail.linked"
    po_id: int
    po_name: str
    graph_message_id: str
    conversation_id: str | None = None
    internet_message_id: str | None = None
    has_attachments: bool = False
    confidence: LinkConfidence
    rule: str = Field(description="which linking rule matched, for audit")


class InboundMailUnlinked(BaseEvent):
    """A message could not be linked by rule; the supplier agent decides (phase 5)."""

    type: Literal["inbound_mail.unlinked"] = "inbound_mail.unlinked"
    graph_message_id: str
    conversation_id: str | None = None
    sender_address: str | None = Field(default=None, description="address only, never the name")
    partner_id: int | None = None
    open_po_names: list[str] = []
    has_attachments: bool = False


class ScheduledTick(BaseEvent):
    """The scheduler fired a job. ``case_id`` is the run id so all work nests under it."""

    type: Literal["scheduler.tick"] = "scheduler.tick"
    job_id: str
    run_id: str
    scheduled_at: AwareDatetime
    trigger: Literal["cron", "manual"] = "cron"


# --- phase 6: Odoo business events and agent completions ------------------------
#
# Odoo emits these from the sc_agents addon (automation rules and the approval
# model), signed with the events secret like every other producer. Payloads are
# record ids, names, dates and amounts: the orchestrator reads details by id.


class OdooPurchaseConfirmed(BaseEvent):
    """A purchase order reached state ``purchase`` (the promise to the supplier is made)."""

    type: Literal["odoo.purchase_confirmed"] = "odoo.purchase_confirmed"
    po_id: int
    po_name: str
    partner_id: int
    date_planned: AwareDatetime | None = None
    amount_total: float = 0.0
    currency: str | None = None
    line_count: int = Field(default=0, ge=0)


class OdooReceiptValidated(BaseEvent):
    """An incoming picking was set to ``done``."""

    type: Literal["odoo.receipt_validated"] = "odoo.receipt_validated"
    picking_id: int
    picking_name: str
    po_id: int | None = None
    po_name: str | None = None
    partner_id: int | None = None
    date_done: AwareDatetime | None = None


class OdooOrderpointTriggered(BaseEvent):
    """A reorder rule's forecast fell below its minimum (phase 7 planning input)."""

    type: Literal["odoo.orderpoint_triggered"] = "odoo.orderpoint_triggered"
    orderpoint_id: int
    product_id: int
    product_code: str | None = None
    qty_to_order: float = Field(ge=0)


class OdooApprovalResolved(BaseEvent):
    """A human resolved an ``sc.approval``; mirrored so the case shows the decision."""

    type: Literal["odoo.approval_resolved"] = "odoo.approval_resolved"
    approval_id: int
    kind: str
    status: Literal["approved", "rejected", "expired"]
    thread_id: str | None = Field(default=None, description="the agent thread that waits")
    po_id: int | None = None
    po_name: str | None = None
    resolved_by: str | None = Field(default=None, description="Odoo login, for the audit line")
    resolved_by_name: str | None = Field(default=None, description="the person, when known")
    resolved_via: Literal["odoo", "api"] | None = None


class AgentRunFinished(BaseEvent):
    """An agent finished a run it had paused on an approval (director was not on that hop)."""

    type: Literal["agent.run_finished"] = "agent.run_finished"
    agent: str
    thread_id: str = Field(description="the task's case_id, i.e. the agent thread")
    run_id: str
    task_kind: str
    status: str
    summary: str = Field(max_length=500)
    po_name: str | None = None
    approval_id: int | None = None
    sent_message_id: str | None = Field(
        default=None, description="Graph id of the email sent after the resume, if any"
    )


class RfqDrafted(BaseEvent):
    """The planner created a draft RFQ; the supplier agent sends it (phase 7)."""

    type: Literal["rfq.drafted"] = "rfq.drafted"
    po_id: int
    po_name: str
    partner_id: int
    run_id: str
    line_count: int = Field(default=0, ge=0)


AnyEvent = Annotated[
    InboundMailLinked
    | InboundMailUnlinked
    | ScheduledTick
    | OdooPurchaseConfirmed
    | OdooReceiptValidated
    | OdooOrderpointTriggered
    | OdooApprovalResolved
    | AgentRunFinished
    | RfqDrafted,
    Field(discriminator="type"),
]

EVENT_TYPES: tuple[type[BaseEvent], ...] = (
    InboundMailLinked,
    InboundMailUnlinked,
    ScheduledTick,
    OdooPurchaseConfirmed,
    OdooReceiptValidated,
    OdooOrderpointTriggered,
    OdooApprovalResolved,
    AgentRunFinished,
    RfqDrafted,
)

_adapter: TypeAdapter[Any] = TypeAdapter(AnyEvent)


def parse_event(data: dict[str, Any] | bytes | str) -> BaseEvent:
    """Parse a payload into the concrete event; unknown ``type`` or extra fields fail."""
    if isinstance(data, dict):
        return _adapter.validate_python(data)  # type: ignore[no-any-return]
    return _adapter.validate_json(data)  # type: ignore[no-any-return]


def event_id_for(event_type: str, *parts: Any) -> str:
    """Deterministic id so a re-emitted event (retry, resync) is a duplicate, not a repeat."""
    return deterministic_id("evt", event_type, *parts)
