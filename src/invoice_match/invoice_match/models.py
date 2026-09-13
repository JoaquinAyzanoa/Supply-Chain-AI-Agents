"""What the agent knows while it works (kept in the state as dicts)."""

from __future__ import annotations

from datetime import date

from sc_core.schema.base import StrictModel


class BillLineView(StrictModel):
    """A line of a vendor bill someone typed in Odoo."""

    description: str
    product_id: int | None = None
    product_ref: str | None = None
    qty: float = 0.0
    price_unit: float = 0.0
    total: float = 0.0
    purchase_line_id: int | None = None


class BillView(StrictModel):
    """A vendor bill as it exists in Odoo (the ``move_id`` path)."""

    move_id: int
    name: str | None = None
    ref: str | None = None
    invoice_date: date | None = None
    partner_id: int | None = None
    partner_name: str | None = None
    amount_untaxed: float = 0.0
    amount_total: float = 0.0
    currency: str | None = None
    state: str
    po_name: str | None = None
    lines: list[BillLineView]


class PoCandidate(StrictModel):
    """An order of the supplier that an invoice without a reference may belong to."""

    id: int
    name: str
    state: str
    amount_total: float
    amount_untaxed: float
    currency: str | None = None
