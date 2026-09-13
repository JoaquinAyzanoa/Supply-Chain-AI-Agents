"""What the agent knows about a receipt while it works (kept in the state as dicts)."""

from __future__ import annotations

from datetime import date, datetime

from sc_core.schema.base import StrictModel


class ReceiptLineView(StrictModel):
    """One move on the receipt: what was expected and what the clerk counted."""

    move_id: int
    po_line_id: int | None = None
    product: str
    product_id: int | None = None
    expected: float  # the move's demand on this receipt
    received: float  # the sum of its counted move lines
    uom: str | None = None
    ordered: float | None = None  # the order line's quantity
    received_total: float | None = None  # the order line's quantity received so far


class ReceiptView(StrictModel):
    picking_id: int
    name: str
    state: str
    po_id: int | None = None
    po_name: str | None = None
    partner_id: int | None = None
    partner_name: str | None = None
    scheduled_date: date | None = None
    date_done: datetime | None = None
    lines: list[ReceiptLineView]
