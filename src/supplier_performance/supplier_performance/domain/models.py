"""The history the metrics read (gathered by the ports, kept in the state as dicts)."""

from __future__ import annotations

from datetime import date, datetime

from sc_core.schema.base import StrictModel


class ReceivedLine(StrictModel):
    """One order line as it was promised and as it arrived."""

    po_name: str
    po_line_id: int
    product_id: int | None = None
    ordered: float
    received: float
    confirmed_at: datetime
    promised_date: date | None = None  # the first promise, from the confirmation snapshot
    received_at: datetime  # the last receipt on the line


class MailPair(StrictModel):
    """An email we sent and the supplier's next reply on the same order."""

    sent_at: datetime
    replied_at: datetime | None = None


class PriceSeries(StrictModel):
    product_id: int | None = None
    prices: list[float]


class SupplierHistory(StrictModel):
    partner_id: int
    partner_name: str
    period_start: date
    period_end: date
    lines: list[ReceivedLine] = []
    mails: list[MailPair] = []
    discrepancy_lines: int = 0
    prices: list[PriceSeries] = []
