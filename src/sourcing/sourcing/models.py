"""What the sourcing agent keeps about rounds, invitations and negotiations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from sc_core.schema.a2a import QuoteLine
from sc_core.schema.base import StrictModel

RoundStatus = Literal[
    "open",  # invitations out, quotes may arrive
    "comparing",  # a comparison is being built
    "awaiting_award",  # the award approval is with a person
    "awarded",
    "rejected",  # the person did not award; the round stays for a re-run or a cancel
    "cancelled",
    "failed",
]
RfqStatus = Literal["created", "sent", "awaiting_approval", "no_email", "failed", "declined"]


class OrderRef(StrictModel):
    """An order or RFQ as the agent needs it: who, what state, which currency."""

    po_id: int
    po_name: str
    partner_id: int
    partner_name: str
    state: str
    currency: str | None = None
    currency_id: int | None = None


class BasketLine(StrictModel):
    """One product the round asks for, with what we last paid for it."""

    product_id: int
    product: str
    qty: float = Field(gt=0)
    last_paid: float | None = Field(default=None, ge=0)
    currency: str | None = None


class SupplierOption(StrictModel):
    """A supplier who could be invited: from the ranking, the price list and the partner."""

    partner_id: int
    partner_name: str
    has_email: bool = False
    score: float | None = None
    rank: int = 0
    price: float | None = Field(default=None, ge=0, description="list price for the product")
    currency: str | None = None
    min_qty: float = 0.0
    lead_days: int | None = None
    first_time: bool = False
    why: str = ""


class PriceEntry(StrictModel):
    """One price-list line: what a supplier lists a product at."""

    partner_id: int
    product_id: int
    price: float = Field(ge=0)
    currency: str | None = None
    min_qty: float = 0.0
    lead_days: int | None = None


class RfqSnapshot(StrictModel):
    """An invited RFQ as Odoo and the mail links show it now."""

    po_id: int
    po_name: str
    partner_id: int
    partner_name: str
    state: str
    currency: str | None = None
    lines: list[QuoteLine] = Field(default_factory=list)
    replied_at: datetime | None = None
    sent_at: datetime | None = None


class RoundRfq(StrictModel):
    partner_id: int
    partner_name: str
    po_id: int | None = None
    po_name: str | None = None
    status: RfqStatus = "created"
    thread_id: str | None = None
    sent_at: datetime | None = None
    replied_at: datetime | None = None


class Round(StrictModel):
    id: int
    case_id: str
    status: RoundStatus = "open"
    source_po_name: str | None = None
    incumbent_partner_id: int | None = None
    basket: list[BasketLine] = Field(default_factory=list)
    deadline: datetime
    group_id: int | None = None
    rfqs: list[RoundRfq] = Field(default_factory=list)
    comparison: dict[str, Any] | None = None
    award_approval_id: int | None = None
    awarded_partner_id: int | None = None
    awarded_po_name: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def active(self) -> bool:
        return self.status in ("open", "comparing", "awaiting_award", "rejected")

    @property
    def products(self) -> str:
        return ", ".join(line.product for line in self.basket)


class Negotiation(StrictModel):
    id: int
    case_id: str
    po_id: int
    po_name: str
    partner_id: int
    product_id: int
    round_no: int
    current_price: float
    offered_price: float
    floor_price: float
    target_price: float
    status: Literal["proposed", "sent", "rejected", "failed"] = "proposed"
    approval_id: int | None = None
    created_at: datetime


__all__ = [
    "BasketLine",
    "Negotiation",
    "OrderRef",
    "PriceEntry",
    "RfqSnapshot",
    "RfqStatus",
    "Round",
    "RoundRfq",
    "RoundStatus",
    "SupplierOption",
]
