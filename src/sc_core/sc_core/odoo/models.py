"""Typed views of the Odoo records the agents read and write.

Odoo's RPC layer returns rows with a few conventions that would leak
everywhere if repositories passed them on raw:

- an empty value of any type is ``False`` (not ``None``)
- a many2one is ``[id, display_name]`` (or ``False``)
- a one2many/many2many is a list of ids
- datetimes are naive strings in UTC (``"2026-09-08 20:21:42"``), dates are
  ``"YYYY-MM-DD"``

``OdooModel`` normalises all of that once, in a ``before`` validator, so a
model declares plain Python types (``Ref | None``, ``datetime | None``) and
repositories stay free of ``if value is False`` checks. Field names match
Odoo's so ``odoo_fields()`` can be passed straight to ``search_read``.

Amounts and quantities are kept as floats here because that is what Odoo
returns; conversion to ``Money`` happens in the agents' domain models.
"""

from __future__ import annotations

import types
from datetime import UTC, date, datetime
from typing import Any, ClassVar, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sc_core.schema.base import StrictModel
from sc_core.shared.time import parse_iso, to_utc


class Ref(StrictModel):
    """A many2one value: id plus display name."""

    id: int
    name: str


def _accepts(annotation: Any, target: type) -> bool:
    """True when ``annotation`` is ``target`` or a union containing it."""
    if annotation is target:
        return True
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        return any(_accepts(arg, target) for arg in get_args(annotation))
    return False


class OdooModel(BaseModel):
    """Base for records read from Odoo. Unknown keys are ignored, values normalised."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    ODOO_MODEL: ClassVar[str] = ""

    id: int

    @classmethod
    def odoo_fields(cls) -> list[str]:
        """Field names to request from Odoo (every declared field but ``id``)."""
        return [name for name in cls.model_fields if name != "id"]

    @classmethod
    def from_odoo(cls, row: dict[str, Any]) -> Any:
        return cls.model_validate(row)

    @model_validator(mode="before")
    @classmethod
    def _normalise_odoo_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out: dict[str, Any] = {}
        for name, value in data.items():
            field = cls.model_fields.get(name)
            if field is None:
                continue
            annotation = field.annotation
            if value is False and not _accepts(annotation, bool):
                value = None
            elif (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], int)
                and isinstance(value[1], str)
                and _accepts(annotation, Ref)
            ):
                value = {"id": value[0], "name": value[1]}
            elif isinstance(value, str) and _accepts(annotation, datetime):
                value = parse_iso(value)  # naive Odoo string -> aware UTC
            out[name] = value
        return out


# --- serialisation helpers for writes -------------------------------------


def to_odoo_datetime(value: datetime) -> str:
    """Odoo wants naive UTC ``YYYY-MM-DD HH:MM:SS``."""
    return to_utc(value).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def to_odoo_date(value: date) -> str:
    return value.isoformat()


def odoo_now() -> str:
    return to_odoo_datetime(datetime.now(UTC))


# --- purchase -------------------------------------------------------------

PurchaseState = Literal["draft", "sent", "to approve", "purchase", "done", "cancel"]
ReceiptStatus = Literal["pending", "partial", "full"]
EtaSource = Literal["supplier", "tracking", "estimated"]


class PurchaseOrder(OdooModel):
    ODOO_MODEL: ClassVar[str] = "purchase.order"

    name: str
    state: PurchaseState
    partner_id: Ref
    partner_ref: str | None = None
    origin: str | None = None
    date_order: datetime | None = None
    date_approve: datetime | None = None
    date_planned: datetime | None = None
    amount_untaxed: float = 0.0
    amount_total: float = 0.0
    currency_id: Ref | None = None
    company_id: Ref | None = None
    user_id: Ref | None = None
    receipt_status: ReceiptStatus | None = None
    order_line: list[int] = []
    picking_ids: list[int] = []
    sc_external_ref: str | None = None
    sc_eta_source: EtaSource | None = None
    sc_eta_confidence: float = 0.0
    sc_needs_human: bool = False
    sc_pending_approval_id: Ref | None = None

    @property
    def is_open(self) -> bool:
        return self.state == "purchase" and self.receipt_status != "full"

    @property
    def is_rfq(self) -> bool:
        return self.state in ("draft", "sent", "to approve")


class PurchaseOrderLine(OdooModel):
    ODOO_MODEL: ClassVar[str] = "purchase.order.line"

    order_id: Ref
    name: str
    product_id: Ref | None = None
    product_qty: float = 0.0
    product_uom: Ref | None = None
    price_unit: float = 0.0
    price_subtotal: float = 0.0
    currency_id: Ref | None = None
    date_planned: datetime | None = None
    qty_received: float = 0.0
    qty_invoiced: float = 0.0
    state: PurchaseState | None = None

    @property
    def qty_pending(self) -> float:
        return max(self.product_qty - self.qty_received, 0.0)


class NewOrderLine(StrictModel):
    """A line to create on a new RFQ. ``price_unit`` None lets Odoo pick the supplier price."""

    product_id: int
    product_qty: float
    price_unit: float | None = None
    date_planned: datetime | None = None
    name: str | None = None

    def to_odoo(self) -> dict[str, Any]:
        values: dict[str, Any] = {"product_id": self.product_id, "product_qty": self.product_qty}
        if self.price_unit is not None:
            values["price_unit"] = self.price_unit
        if self.date_planned is not None:
            values["date_planned"] = to_odoo_datetime(self.date_planned)
        if self.name is not None:
            values["name"] = self.name
        return values


class SupplierInfo(OdooModel):
    ODOO_MODEL: ClassVar[str] = "product.supplierinfo"

    partner_id: Ref
    product_tmpl_id: Ref | None = None
    product_id: Ref | None = None
    product_name: str | None = None
    product_code: str | None = None
    min_qty: float = 0.0
    price: float = 0.0
    currency_id: Ref | None = None
    delay: int = 0
    date_start: date | None = None
    date_end: date | None = None
    sequence: int = 1


# --- stock ----------------------------------------------------------------

PickingState = Literal["draft", "waiting", "confirmed", "assigned", "done", "cancel"]
PickingTypeCode = Literal["incoming", "outgoing", "internal"]


class Orderpoint(OdooModel):
    ODOO_MODEL: ClassVar[str] = "stock.warehouse.orderpoint"

    name: str
    product_id: Ref
    warehouse_id: Ref | None = None
    location_id: Ref | None = None
    product_min_qty: float = 0.0
    product_max_qty: float = 0.0
    qty_to_order: float = 0.0
    qty_forecast: float = 0.0
    qty_on_hand: float = 0.0
    trigger: Literal["auto", "manual"] = "auto"
    lead_days_date: date | None = None
    supplier_id: Ref | None = None
    active: bool = True


class Picking(OdooModel):
    ODOO_MODEL: ClassVar[str] = "stock.picking"

    name: str
    state: PickingState
    partner_id: Ref | None = None
    origin: str | None = None
    purchase_id: Ref | None = None
    picking_type_code: PickingTypeCode | None = None
    scheduled_date: datetime | None = None
    date_deadline: datetime | None = None
    date_done: datetime | None = None
    move_ids: list[int] = []
    sc_eta_source: EtaSource | None = None
    sc_needs_human: bool = False
    sc_last_run_id: str | None = None


class StockMove(OdooModel):
    ODOO_MODEL: ClassVar[str] = "stock.move"

    product_id: Ref
    product_uom_qty: float = 0.0  # demanded
    quantity: float = 0.0  # done / reserved
    state: str
    picking_id: Ref | None = None
    purchase_line_id: Ref | None = None
    date: datetime | None = None


# --- partners and activities -------------------------------------------------


class Partner(OdooModel):
    ODOO_MODEL: ClassVar[str] = "res.partner"

    name: str
    email: str | None = None
    email_normalized: str | None = None
    is_company: bool = False
    supplier_rank: int = 0
    parent_id: Ref | None = None
    commercial_partner_id: Ref | None = None
    child_ids: list[int] = []
    lang: str | None = None
    active: bool = True

    @property
    def email_domain(self) -> str | None:
        email = self.email_normalized or self.email
        return email.rsplit("@", 1)[1].lower() if email and "@" in email else None


class Activity(OdooModel):
    ODOO_MODEL: ClassVar[str] = "mail.activity"

    res_model: str
    res_id: int
    activity_type_id: Ref | None = None
    summary: str | None = None
    note: str | None = None
    date_deadline: date | None = None
    user_id: Ref | None = None
    state: Literal["overdue", "today", "planned", "done"] | None = None


# --- sc_agents addon ----------------------------------------------------------

ApprovalKind = Literal[
    "send_email", "po_change", "orderpoint_change", "planning_run", "unlinked_mail", "escalation"
]
ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]
RunStatus = Literal[
    "running",
    "sent",
    "applied",
    "awaiting_approval",
    "rejected",
    "no_action",
    "escalated",
    "failed",
]
MailDirection = Literal["in", "out"]
LinkConfidence = Literal["exact", "sender_single_open_po", "agent", "human"]


class Approval(OdooModel):
    ODOO_MODEL: ClassVar[str] = "sc.approval"

    kind: ApprovalKind
    summary: str
    status: ApprovalStatus
    res_model: str | None = None
    res_id: int | None = None
    po_id: Ref | None = None
    payload_json: str | None = None
    requested_by: str | None = None
    case_id: str | None = None
    run_id: str | None = None
    thread_id: str | None = None
    resolved_by_id: Ref | None = None
    resolved_by_name: str | None = None
    resolved_via: Literal["odoo", "api"] | None = None
    resolved_at: datetime | None = None
    reason: str | None = None
    details_json: str | None = None
    callback_status: Literal["none", "sent", "failed"] | None = None
    callback_error: str | None = None
    create_date: datetime | None = None

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"


class AgentRun(OdooModel):
    ODOO_MODEL: ClassVar[str] = "sc.agent.run"

    run_id: str
    agent: str
    case_id: str | None = None
    po_id: Ref | None = None
    status: RunStatus
    model: str | None = None
    trace_url: str | None = None
    summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class MailLink(OdooModel):
    ODOO_MODEL: ClassVar[str] = "sc.mail.link"

    po_id: Ref
    direction: MailDirection
    graph_message_id: str
    graph_conversation_id: str | None = None
    internet_message_id: str | None = None
    received_at: datetime | None = None
    web_link: str | None = None
    case_id: str | None = None
    confidence: LinkConfidence | None = None


class Product(OdooModel):
    ODOO_MODEL: ClassVar[str] = "product.product"

    name: str
    default_code: str | None = None
    product_tmpl_id: Ref
    uom_id: Ref | None = None
    categ_id: Ref | None = None
    seller_ids: list[int] = []
    qty_available: float = 0.0
    virtual_available: float = 0.0
    list_price: float = 0.0
    standard_price: float = 0.0
    purchase_ok: bool = True
    sale_ok: bool = True
    is_storable: bool = False
    description: str | None = None  # internal notes (a "descontinuado" note lives here)
    active: bool = True

    @property
    def ref(self) -> str:
        return self.default_code or str(self.id)


# --- inventory planning (phase 7) ----------------------------------------------------


class Warehouse(OdooModel):
    ODOO_MODEL: ClassVar[str] = "stock.warehouse"

    name: str
    code: str
    lot_stock_id: Ref | None = None


class Quant(OdooModel):
    ODOO_MODEL: ClassVar[str] = "stock.quant"

    product_id: Ref
    location_id: Ref | None = None
    warehouse_id: Ref | None = None
    quantity: float = 0.0
    reserved_quantity: float = 0.0


class DailyDemand(StrictModel):
    """One product, one day: what customers ordered and what was delivered."""

    product_id: int
    day: date
    ordered: float = Field(ge=0)
    delivered: float = Field(ge=0)


class OnHand(StrictModel):
    product_id: int
    warehouse_id: int
    quantity: float
    reserved: float = 0.0

    @property
    def free(self) -> float:
        return self.quantity - self.reserved


class IncomingLine(StrictModel):
    """A confirmed purchase line with something still to receive."""

    line_id: int
    product_id: int
    po_id: int
    po_name: str
    partner_id: int | None = None
    quantity: float = Field(gt=0, description="still to receive")
    date_planned: date | None = None


class SupplierTerms(StrictModel):
    product_id: int
    partner_id: int
    partner_name: str
    delay_days: int = Field(ge=0)
    min_qty: float = Field(ge=0)
    price: float = Field(ge=0)
    currency: str | None = None
    sequence: int = 1
