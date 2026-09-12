"""Event contract.

Every event that moves between services (mail_sync → director, scheduler →
director, Odoo webhook → director) extends ``BaseEvent``. The base carries
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


AnyEvent = Annotated[
    InboundMailLinked | InboundMailUnlinked | ScheduledTick,
    Field(discriminator="type"),
]

_adapter: TypeAdapter[Any] = TypeAdapter(AnyEvent)


def parse_event(
    data: dict[str, Any] | bytes | str,
) -> InboundMailLinked | InboundMailUnlinked | ScheduledTick:
    """Parse a payload into the concrete event; unknown ``type`` or extra fields fail."""
    if isinstance(data, dict):
        return _adapter.validate_python(data)  # type: ignore[no-any-return]
    return _adapter.validate_json(data)  # type: ignore[no-any-return]


def event_id_for(event_type: str, *parts: Any) -> str:
    """Deterministic id so a re-emitted event (retry, resync) is a duplicate, not a repeat."""
    return deterministic_id("evt", event_type, *parts)
