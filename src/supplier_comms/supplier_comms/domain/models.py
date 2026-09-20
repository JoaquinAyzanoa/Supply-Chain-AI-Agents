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
    qty_invoiced: float = 0.0


class PoContext(StrictModel):
    id: int
    name: str
    state: str
    partner_id: int
    partner_name: str
    partner_lang: str | None = None  # Odoo language of the supplier, e.g. es_PE
    supplier_emails: list[str]
    currency: str | None = None
    currency_id: int | None = None
    date_planned: date | None = None
    amount_total: float = 0.0
    lines: list[LineView]
    prior_mail_links: int = 0


class UnlinkedResolution(BaseModel):
    """The model's pick among candidate orders for a message the rules could not link."""

    po_name: str | None = Field(default=None, description="chosen order; empty when none")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class InboundMeta(StrictModel):
    """Identifiers of an inbound message, never its text: sender and subject token."""

    graph_message_id: str
    sender_address: str | None = None
    sender_name: str | None = None
    subject_token: str | None = None
    has_attachments: bool = False
    web_link: str | None = None
    conversation_id: str | None = None
    internet_message_id: str | None = None


class DraftOutput(BaseModel):
    """What the model must return after drafting."""

    subject: str = Field(min_length=3, max_length=200, description="asunto sin el token de orden")
    html_body: str = Field(min_length=20, description="cuerpo completo en HTML sencillo")


class AnswerOutput(BaseModel):
    """A supplier's question answered from records (phase 11 S6)."""

    subject: str = Field(min_length=3, max_length=200)
    html_body: str = Field(min_length=20, description="simple HTML")
    factual: bool = Field(description="every part of the question is answered by a record")
    sources: list[str] = Field(default_factory=list, description="record references used")
    reason: str | None = Field(default=None, description="what a person must decide, if any")


class RequestedLine(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    product_ref: str | None = None
    qty: float = Field(gt=0)
    uom: str | None = None


class RequestExtraction(BaseModel):
    """What the model reads in an employee's email to purchasing (phase 11 S6)."""

    items: list[RequestedLine] = Field(default_factory=list)
    need_date_raw: str | None = None
    need_date: date | None = None
    requester_name: str | None = None
    notes: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=1.0, ge=0, le=1)
