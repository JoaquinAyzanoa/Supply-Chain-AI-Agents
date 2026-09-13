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
from sc_core.schema.planning import ReplenishmentProposal

# --- tasks ------------------------------------------------------------------------

TaskKind = Literal[
    "send_rfq",
    "request_eta",
    "follow_up",
    "send_po",
    "handle_inbound",
    "resolve_unlinked",
    "decline_quote",  # phase 11: the round went elsewhere
    "counter_offer",  # phase 11: the sourcing agent negotiates through this agent
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
    assigned_po_name: str | None = Field(
        default=None,
        description="resolve_unlinked: a person named the order; link it without asking the model",
    )
    require_approval: bool = Field(
        default=False,
        description="a person asked for this email from the Control Tower: show the draft "
        "for approval before sending, whatever the automatic-send rules say",
    )
    pre_approved: str | None = Field(
        default=None,
        description="the content was approved upstream (an award, a counter-offer): send "
        "without a second approval; the text says which approval",
    )

    @model_validator(mode="after")
    def _required_by_kind(self) -> SupplierCommsTask:
        needs_po = self.kind in (
            "send_rfq",
            "request_eta",
            "follow_up",
            "send_po",
            "handle_inbound",
            "decline_quote",
            "counter_offer",
        )
        needs_message = self.kind in ("handle_inbound", "resolve_unlinked")
        if needs_po and not self.po_name:
            raise ValueError(f"{self.kind} needs po_name")
        if needs_message and not self.graph_message_id:
            raise ValueError(f"{self.kind} needs graph_message_id")
        return self


# --- what the agent produces along the way ------------------------------------------

ClassificationKind = Literal[
    "quotation", "eta_update", "question", "shipping_notice", "invoice", "other"
]


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


DraftKind = Literal[
    "rfq", "request_eta", "follow_up", "send_po", "reply", "discrepancy", "decline", "counter_offer"
]


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


# --- inventory planning (phase 7) ----------------------------------------------------

PlanningTaskKind = Literal["daily_plan", "review_product", "what_if"]


class PlanningOverrides(StrictModel):
    """Parameters a planner may try in a ``what_if`` (no writes) or force in a review."""

    service_level: float | None = Field(default=None, gt=0.5, lt=1.0)
    review_period_days: int | None = Field(default=None, ge=1)
    lead_time_days: float | None = Field(default=None, ge=0)
    max_coverage_days: int | None = Field(default=None, ge=1)


class InventoryPlanningTask(StrictModel):
    """What the director asks the planner to do."""

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: PlanningTaskKind
    case_id: str = Field(min_length=1)
    product_ids: list[int] = Field(
        default_factory=list, description="empty = every plannable product"
    )
    warehouse_code: str | None = None
    as_of: date | None = Field(default=None, description="plan date; today when omitted")
    overrides: PlanningOverrides = Field(default_factory=PlanningOverrides)
    context: str | None = Field(
        default=None, max_length=2000, description="review_product: why the review was asked"
    )

    @model_validator(mode="after")
    def _required_by_kind(self) -> InventoryPlanningTask:
        if self.kind in ("review_product", "what_if") and not self.product_ids:
            raise ValueError(f"{self.kind} needs product_ids")
        return self


class AppliedSummary(StrictModel):
    orderpoints_written: int = 0
    rfqs_created: list[str] = []
    rfqs_existing: list[str] = []
    lines_applied: int = 0


class InventoryPlanningResult(StrictModel):
    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: PlanningTaskKind
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    outcome: Outcome
    proposal: ReplenishmentProposal | None = None
    applied: AppliedSummary | None = None
    trace_id: str | None = None

    @property
    def status(self) -> OutcomeStatus:
        return self.outcome.status


# --- logistics (phase 9) --------------------------------------------------------------

LogisticsTaskKind = Literal["track_shipment", "reconcile_receipt", "report_discrepancy"]


class LogisticsTask(StrictModel):
    """What the director asks the logistics agent to do."""

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: LogisticsTaskKind
    case_id: str = Field(min_length=1)
    po_name: str | None = Field(default=None, description="Odoo order name, e.g. P00015")
    graph_message_id: str | None = Field(
        default=None, description="track_shipment: the supplier's shipping notice"
    )
    picking_id: int | None = Field(
        default=None, description="reconcile_receipt / report_discrepancy: the receipt"
    )
    notes: str | None = Field(
        default=None, max_length=2000, description="report_discrepancy: the clerk's words"
    )
    require_approval: bool = Field(default=False, description="a person asked; show it first")

    @model_validator(mode="after")
    def _required_by_kind(self) -> LogisticsTask:
        if self.kind == "track_shipment" and not (self.po_name and self.graph_message_id):
            raise ValueError("track_shipment needs po_name and graph_message_id")
        if self.kind in ("reconcile_receipt", "report_discrepancy") and self.picking_id is None:
            raise ValueError(f"{self.kind} needs picking_id")
        return self


class ShipmentInfo(StrictModel):
    """What a shipping notice says: who carries it, the number to track, when it arrives."""

    carrier: str | None = Field(default=None, max_length=100)
    tracking_number: str | None = Field(default=None, max_length=100)
    ship_date: date | None = None
    eta_date: date | None = Field(default=None, description="arrival the agent derived")
    eta_date_raw: str | None = Field(default=None, max_length=100)
    partial: bool = False
    packing_list: bool = False
    notes: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=1.0, ge=0, le=1)


DiscrepancyKind = Literal["short", "over", "damaged"]


class ReceiptDiscrepancy(StrictModel):
    po_line_id: int | None = None
    product: str = Field(min_length=1)
    kind: DiscrepancyKind
    expected: float = Field(ge=0)
    received: float = Field(ge=0)
    uom: str | None = None

    @property
    def difference(self) -> float:
        return self.received - self.expected


class ReceiptReconciliation(StrictModel):
    """Counted against expected on one receipt; empty ``discrepancies`` means a match."""

    picking_id: int
    picking_name: str = Field(min_length=1)
    lines: int = Field(ge=0)
    tolerance_pct: float = Field(default=0.0, ge=0)
    discrepancies: list[ReceiptDiscrepancy] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.discrepancies


class LogisticsResult(StrictModel):
    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: LogisticsTaskKind
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    outcome: Outcome
    po_name: str | None = None
    shipment: ShipmentInfo | None = None
    proposal: ChangeProposal | None = None
    reconciliation: ReceiptReconciliation | None = None
    outbound: OutboundSummary | None = None
    trace_id: str | None = None

    @property
    def status(self) -> OutcomeStatus:
        return self.outcome.status


# --- invoice matching (phase 9) ----------------------------------------------------

InvoiceTaskKind = Literal["match_bill"]


class InvoiceMatchTask(StrictModel):
    """What the director asks the invoice matching agent to do."""

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: InvoiceTaskKind
    case_id: str = Field(min_length=1)
    po_name: str | None = Field(default=None, description="the order, when already known")
    graph_message_id: str | None = Field(
        default=None, description="the supplier's email with the invoice attached"
    )
    move_id: int | None = Field(default=None, description="a vendor bill typed in Odoo")
    notes: str | None = Field(default=None, max_length=2000)
    require_approval: bool = Field(default=False, description="a person asked; show it first")

    @model_validator(mode="after")
    def _required_by_kind(self) -> InvoiceMatchTask:
        if not (self.graph_message_id or self.move_id):
            raise ValueError("match_bill needs graph_message_id or move_id")
        return self


class InvoiceLine(StrictModel):
    description: str = Field(min_length=1, max_length=300)
    product_ref: str | None = Field(default=None, max_length=100)
    qty: float | None = Field(default=None, ge=0)
    unit_price: float | None = Field(default=None, ge=0)
    total: float | None = Field(default=None)


class InvoiceData(StrictModel):
    """What the invoice says, as printed (from the PDF, the email or the Odoo bill)."""

    supplier_name: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    po_reference: str | None = Field(default=None, max_length=50)
    lines: list[InvoiceLine] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


MatchStatus = Literal["ok", "price_variance", "qty_variance", "not_received", "unmatched"]


class MatchLine(StrictModel):
    po_line_id: int | None = None
    product: str | None = None
    invoice_description: str = Field(min_length=1)
    invoice_qty: float | None = None
    invoice_price: float | None = None
    po_qty: float | None = None
    po_price: float | None = None
    received_qty: float | None = None
    invoiced_qty: float | None = None
    status: MatchStatus
    note: str | None = None
    similarity: float = Field(default=0.0, ge=0, le=1)


BillVerdict = Literal["clean", "hold"]


class BillMatch(StrictModel):
    po_name: str | None = None
    verdict: BillVerdict
    lines: list[MatchLine] = Field(default_factory=list)
    invoice_total: float | None = None
    invoice_subtotal: float | None = None
    expected_subtotal: float | None = None
    price_tolerance_pct: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class InvoiceMatchResult(StrictModel):
    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: InvoiceTaskKind
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    outcome: Outcome
    po_name: str | None = None
    invoice: InvoiceData | None = None
    match: BillMatch | None = None
    bill_id: int | None = None
    bill_name: str | None = None
    trace_id: str | None = None

    @property
    def status(self) -> OutcomeStatus:
        return self.outcome.status


# --- supplier performance (phase 9) ---------------------------------------------------

PerformanceTaskKind = Literal["weekly_scorecard"]


class SupplierPerformanceTask(StrictModel):
    """What the director asks the performance agent to do (the weekly run)."""

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: PerformanceTaskKind
    case_id: str = Field(min_length=1)
    as_of: date | None = Field(default=None, description="period end; today when omitted")
    partner_ids: list[int] = Field(
        default_factory=list, description="empty = every active supplier"
    )


class SupplierScore(StrictModel):
    """One supplier's numbers for a period, the model's paragraph and the flagged changes."""

    partner_id: int
    partner_name: str = Field(min_length=1)
    period_start: date
    period_end: date
    otif: float | None = Field(default=None, ge=0, le=1)
    lead_time_mean_days: float | None = Field(default=None, ge=0)
    lead_time_sigma_days: float | None = Field(default=None, ge=0)
    promise_drift_days: float | None = None
    response_hours_median: float | None = Field(default=None, ge=0)
    quality_rate: float | None = Field(default=None, ge=0, le=1)
    price_cv: float | None = Field(default=None, ge=0)
    score: float = Field(ge=0, le=100)
    samples: dict[str, int] = Field(default_factory=dict)
    scorecard: str | None = Field(default=None, max_length=1000)
    trends: list[str] = Field(default_factory=list)


class PerformanceApplied(StrictModel):
    partners: int = 0
    price_list_entries: int = 0
    planning_params: int = 0


class SupplierPerformanceResult(StrictModel):
    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: PerformanceTaskKind
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    outcome: Outcome
    period_start: date | None = None
    period_end: date | None = None
    scores: list[SupplierScore] = Field(default_factory=list)
    applied: PerformanceApplied | None = None
    trace_id: str | None = None

    @property
    def status(self) -> OutcomeStatus:
        return self.outcome.status


class RankedSupplier(StrictModel):
    rank: int = 0
    partner_id: int
    partner_name: str
    score: float | None = None
    otif: float | None = None
    lead_time_mean_days: float | None = None
    price: float | None = None
    currency: str | None = None
    min_qty: float = 0.0
    promised_lead_days: int = 0
    samples: dict[str, int] = Field(default_factory=dict)
    why: str = ""


class SupplierRanking(StrictModel):
    product_id: int
    suppliers: list[RankedSupplier] = Field(default_factory=list)


# --- sourcing and negotiation (phase 11) -------------------------------------------

SourcingTaskKind = Literal["quote_round", "compare_quotes", "counter_offer", "alternate_source"]


class SourcingTask(StrictModel):
    """What the director asks the sourcing agent to do.

    A round is described either by ``product_id`` + ``qty`` or by ``po_name``
    (the lines of an existing order or RFQ, its partner being the incumbent).
    """

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: SourcingTaskKind
    case_id: str = Field(min_length=1)
    po_name: str | None = Field(default=None, description="the order the task is about")
    partner_id: int | None = Field(default=None, description="the incumbent supplier, if any")
    product_id: int | None = None
    qty: float | None = Field(default=None, gt=0)
    partner_ids: list[int] = Field(default_factory=list, description="invite these as well")
    exclude_partner_ids: list[int] = Field(default_factory=list)
    round_id: int | None = Field(default=None, description="compare_quotes: the round")
    target_price: float | None = Field(
        default=None, gt=0, description="counter_offer: a buyer's target"
    )
    max_suppliers: int | None = Field(default=None, ge=1, le=10)
    deadline_days: int | None = Field(default=None, ge=1, le=60)
    notes: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=500, description="why the task was raised")

    @model_validator(mode="after")
    def _required_by_kind(self) -> SourcingTask:
        if self.kind == "quote_round" and not (self.po_name or (self.product_id and self.qty)):
            raise ValueError("quote_round needs po_name or product_id and qty")
        if self.kind == "compare_quotes" and not (self.round_id or self.po_name):
            raise ValueError("compare_quotes needs round_id or po_name")
        if self.kind in ("counter_offer", "alternate_source") and not self.po_name:
            raise ValueError(f"{self.kind} needs po_name")
        return self


class QuoteLine(StrictModel):
    """One product as one supplier quoted it (or lists it), with its landed cost."""

    product_id: int
    product: str
    qty: float = Field(gt=0)
    price_unit: float | None = Field(default=None, ge=0)
    landed_unit: float | None = Field(default=None, ge=0)
    lead_days: int | None = Field(default=None, ge=0)
    min_qty: float = 0.0


QuoteSource = Literal["reply", "price_list", "none"]


class ComparedQuote(StrictModel):
    """A supplier's offer over the round's basket, ranked against the others."""

    partner_id: int
    partner_name: str
    po_name: str | None = Field(default=None, description="the RFQ sent to this supplier")
    source: QuoteSource = "none"
    currency: str | None = None
    lines: list[QuoteLine] = Field(default_factory=list)
    total: float | None = Field(default=None, ge=0, description="landed total over the basket")
    lead_days: int | None = Field(default=None, ge=0, description="longest line lead time")
    score: float | None = Field(default=None, ge=0, le=100)
    complete: bool = True
    rank: int = 0
    composite: float | None = None
    reasons: list[str] = Field(default_factory=list)
    recommended: bool = False
    first_time_supplier: bool = False


class QuoteComparison(StrictModel):
    round_id: int
    source_po_name: str | None = None
    basket: list[QuoteLine] = Field(default_factory=list, description="what was asked, per product")
    quotes: list[ComparedQuote] = Field(default_factory=list)
    recommended_partner_id: int | None = None
    recommendation: str = ""
    freight_pct: float = 0.0
    weights: dict[str, float] = Field(default_factory=dict)
    last_paid: dict[str, float] = Field(default_factory=dict, description="per product id")
    invited: int = 0
    replied: int = 0


class CounterOffer(StrictModel):
    """A negotiation move on one quoted line: what we ask, why, and the limits."""

    po_name: str
    partner_id: int
    partner_name: str
    product_id: int
    product: str
    qty: float = Field(gt=0)
    currency: str | None = None
    current_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    floor_price: float = Field(gt=0, description="never ask below this (cap on the move)")
    offered_price: float = Field(gt=0)
    cap_pct: float = Field(ge=0)
    round_no: int = Field(ge=1)
    max_rounds: int = Field(ge=1)
    basis: str = Field(default="", description="where the target comes from")
    justification: str = ""


class InvitedRfq(StrictModel):
    partner_id: int
    partner_name: str
    po_name: str | None = None
    status: str = "created"  # created | sent | awaiting_approval | no_email | failed


class SourcingResult(StrictModel):
    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    kind: SourcingTaskKind
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    outcome: Outcome
    round_id: int | None = None
    invited: list[InvitedRfq] = Field(default_factory=list)
    comparison: QuoteComparison | None = None
    counter_offer: CounterOffer | None = None
    awarded_po_name: str | None = None
    trace_id: str | None = None

    @property
    def status(self) -> OutcomeStatus:
        return self.outcome.status


CONTRACTS: dict[str, type[StrictModel]] = {
    "sourcing_task": SourcingTask,
    "sourcing_result": SourcingResult,
    "quote_comparison": QuoteComparison,
    "supplier_performance_task": SupplierPerformanceTask,
    "supplier_performance_result": SupplierPerformanceResult,
    "invoice_match_task": InvoiceMatchTask,
    "invoice_match_result": InvoiceMatchResult,
    "logistics_task": LogisticsTask,
    "logistics_result": LogisticsResult,
    "supplier_comms_task": SupplierCommsTask,
    "supplier_comms_result": SupplierCommsResult,
    "inventory_planning_task": InventoryPlanningTask,
    "inventory_planning_result": InventoryPlanningResult,
    "replenishment_proposal": ReplenishmentProposal,
}
