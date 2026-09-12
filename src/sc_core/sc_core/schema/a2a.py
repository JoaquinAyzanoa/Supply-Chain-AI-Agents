"""Task and result contracts between the director and the agents (v1).

These cross the A2A boundary as JSON, so they are strict, versioned and
snapshotted (``tests/fixtures/schemas``): a change that alters the JSON
schema fails the snapshot test and has to be deliberate.

Rule kept from the events: identifiers and structured facts only. The
supplier's email text never travels in a task or a result; the agent
fetches it from Graph by ``graph_message_id`` and clears it before the run
ends. What the agent itself drafted (subject, recipients) may be summarised.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from sc_core.schema.base import StrictModel

# --- tasks ------------------------------------------------------------------------

TaskKind = Literal[
    "send_rfq", "request_eta", "follow_up", "send_po", "handle_inbound", "resolve_unlinked"
]


class SupplierCommsTask(StrictModel):
    """What the director asks the supplier communications agent to do."""

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: TaskKind
    case_id: str = Field(min_length=1)
    po_name: str | None = Field(default=None, description="Odoo order name, e.g. P00015")
    graph_message_id: str | None = Field(default=None, description="inbound message to handle")
    notes: str | None = Field(default=None, description="buyer's instructions for the draft")
    days_silent: int | None = Field(default=None, ge=0, description="follow_up: days without reply")
    candidate_po_names: list[str] = Field(
        default_factory=list, description="resolve_unlinked: orders the sender may refer to"
    )

    @model_validator(mode="after")
    def _required_by_kind(self) -> SupplierCommsTask:
        needs_po = self.kind in (
            "send_rfq",
            "request_eta",
            "follow_up",
            "send_po",
            "handle_inbound",
        )
        needs_message = self.kind in ("handle_inbound", "resolve_unlinked")
        if needs_po and not self.po_name:
            raise ValueError(f"{self.kind} needs po_name")
        if needs_message and not self.graph_message_id:
            raise ValueError(f"{self.kind} needs graph_message_id")
        return self


# --- what the agent produces along the way ------------------------------------------

ClassificationKind = Literal["quotation", "eta_update", "question", "other"]


class Classification(StrictModel):
    kind: ClassificationKind
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class QuotedLine(StrictModel):
    """One product as the supplier quoted it, mapped to our order line when possible."""

    po_line_id: int | None = Field(default=None, description="matching purchase.order.line id")
    product_ref: str | None = Field(default=None, description="supplier's product reference")
    description: str = Field(min_length=1)
    qty: float | None = Field(default=None, ge=0)
    unit_price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    lead_days: int | None = Field(default=None, ge=0)
    min_qty: float | None = Field(default=None, ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)


class QuotationData(StrictModel):
    """Structured content of a supplier reply: prices, lead times and the delivery date."""

    lines: list[QuotedLine] = Field(default_factory=list)
    eta_date_raw: str | None = Field(default=None, description="the date as the supplier wrote it")
    eta_date: date | None = Field(default=None, description="ISO date the agent derived")
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=1.0, ge=0, le=1)


ChangeField = Literal["date_planned", "price", "lead_days"]


class ProposedChange(StrictModel):
    po_line_id: int
    product: str
    field: ChangeField
    before: str | None
    after: str
    source: Literal["supplier", "tracking", "estimated"] = "supplier"
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = False
    review_reason: str | None = None


class ChangeProposal(StrictModel):
    changes: list[ProposedChange]
    summary: str = Field(min_length=1, max_length=500)

    @property
    def needs_review(self) -> bool:
        return any(c.needs_review for c in self.changes)

    @property
    def applicable(self) -> list[ProposedChange]:
        return [c for c in self.changes if not c.needs_review]


DraftKind = Literal["rfq", "request_eta", "follow_up", "send_po", "reply"]


class OutboundDraft(StrictModel):
    """An email the agent wrote. Lives in the state; results carry ``OutboundSummary``."""

    kind: DraftKind
    to: list[str] = Field(min_length=1)
    subject: str = Field(min_length=1)
    html_body: str = Field(min_length=1)
    reply_to_message_id: str | None = Field(
        default=None, description="Graph id of the message replied to"
    )
    draft_id: str | None = Field(default=None, description="Graph id of the created draft")


class OutboundSummary(StrictModel):
    kind: DraftKind
    to: list[str]
    subject: str
    attachments: list[str] = []
    draft_id: str | None = None
    sent_message_id: str | None = None
    web_link: str | None = None


# --- results ------------------------------------------------------------------------

OutcomeStatus = Literal[
    "sent", "applied", "awaiting_approval", "rejected", "no_action", "escalated", "failed"
]


class Outcome(StrictModel):
    status: OutcomeStatus
    summary: str = Field(min_length=1, max_length=500)
    approval_id: int | None = None


class SupplierCommsResult(StrictModel):
    """What the agent returns to the director (also when paused on an approval)."""

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: TaskKind
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    outcome: Outcome
    po_name: str | None = None
    chosen_po_name: str | None = Field(default=None, description="resolve_unlinked: the pick")
    classification: Classification | None = None
    extracted: QuotationData | None = None
    proposal: ChangeProposal | None = None
    outbound: OutboundSummary | None = None
    trace_id: str | None = None

    @property
    def status(self) -> OutcomeStatus:
        return self.outcome.status


CONTRACTS: dict[str, type[StrictModel]] = {
    "supplier_comms_task": SupplierCommsTask,
    "supplier_comms_result": SupplierCommsResult,
}
