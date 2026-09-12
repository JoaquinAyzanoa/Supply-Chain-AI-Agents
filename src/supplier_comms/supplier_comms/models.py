"""What the agent knows about an order while it works (kept in the state as dicts)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from sc_core.schema.base import StrictModel


class LineView(StrictModel):
    id: int
    product: str
    product_id: int | None = None
    product_tmpl_id: int | None = None
    qty: float
    uom: str | None = None
    price_unit: float
    currency: str | None = None
    date_planned: date | None = None
    qty_received: float = 0.0


class PoContext(StrictModel):
    id: int
    name: str
    state: str
    partner_id: int
    partner_name: str
    supplier_emails: list[str]
    currency: str | None = None
    currency_id: int | None = None
    date_planned: date | None = None
    amount_total: float = 0.0
    lines: list[LineView]
    prior_mail_links: int = 0


class DraftOutput(BaseModel):
    """What the model must return after drafting."""

    subject: str = Field(min_length=3, max_length=200, description="asunto sin el token de orden")
    html_body: str = Field(min_length=20, description="cuerpo completo en HTML sencillo")
