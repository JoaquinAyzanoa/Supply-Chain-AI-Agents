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

from typing import ClassVar

from pydantic import AwareDatetime, Field

from sc_core.schema.base import StrictModel
from sc_core.shared.idempotency import new_id
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
