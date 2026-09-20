"""Demo mode (phase 11 S9): a scripted ten-minute scenario on the live stack.

The script is a fixed list of steps. Each one either asks the department to do
something (an ETA request, a quote round, a comparison, the briefing) or plays
the world outside it: the supplier answering from its own mailbox, the warehouse
receiving short, accounting typing a bill with a price variance. The department
side runs through the same agents, approvals and events as any other day; the
world side goes through two ports:

* ``SupplierMailbox`` sends the supplier's replies by SMTP from the demo
  supplier's mailbox (Gmail with an app password). They land in the bot's
  inbox, mail_sync links them by the order token in the subject and the
  agents read them like any other email.
* ``DemoWorld`` does what the bot deliberately cannot: validate a receipt and
  type a vendor bill. It uses an Odoo login with those rights (local only).

The position and the outcomes are kept in one ``demo_state`` row so the Demo
page and ``scripts/demo_day.py`` show the same script. A step that is still
waiting for the mailbox or an agent keeps the position; running it again does
not resend anything (what was sent is remembered in ``records``).
"""

from __future__ import annotations

import asyncio
import json
import math
import smtplib
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from loguru import logger
from pydantic import Field

from director.store import CaseStore
from director.workflow import Deps, consolidate_outcome, outcome_from_reply
from sc_core.infra.db import Database
from sc_core.infra.settings import DemoCfg
from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import NewOrderLine
from sc_core.odoo.repositories import (
    MailLinkRepo,
    PartnerRepo,
    PickingRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
)
from sc_core.schema.a2a import SourcingTask, SupplierCommsTask
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.idempotency import new_id
from sc_core.shared.time import local_today, utc_now

if TYPE_CHECKING:  # the API package imports this module's router: annotations only
    from director.api.approvals import ApprovalsGateway
    from director.api.mailbox import MailboxSync
    from director.api.risk import RiskSource
    from director.briefing import BriefingJob
    from director.sourcing import SourcingDispatcher, SourcingSource

StepStatus = Literal["done", "waiting", "failed"]
StepNeeds = Literal["none", "mailbox", "world"]


# --- what the suppliers write -----------------------------------------------------------
# Rebuilt from the run's records whenever someone wants to read them: no email text is kept.

DemoLanguage = Literal["en", "es"]

# how the two other suppliers quote: price factor on their own list, lead days, a note per language
RIVAL_TERMS: dict[str, tuple[float, int, dict[str, str]]] = {
    "alterna": (
        1.00,
        12,
        {
            "en": "Stock in Arequipa; we can ship within the week.",
            "es": "Stock en Arequipa; podemos despachar esta misma semana.",
        },
    ),
    "importadora": (
        0.97,
        55,
        {
            "en": "Price valid for full cartons; shipped by sea from the manufacturer.",
            "es": "Precio válido por cajas completas; embarque marítimo desde el fabricante.",
        },
    ),
}
SUBJECTS = {
    "en": {"eta": "Delivery date", "quote": "Quotation"},
    "es": {"eta": "Fecha de entrega", "quote": "Cotización"},
}
MONTHS_ES = (
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre"
).split()


def subject_for(kind: str, po_name: str, language: str = "en") -> str:
    return f"Re: [{po_name}] {SUBJECTS.get(language, SUBJECTS['en'])[kind]}"


def rival_terms(supplier_name: str, language: str = "en") -> tuple[float, int, str]:
    lowered = supplier_name.lower()
    for key, (factor, lead_days, notes) in RIVAL_TERMS.items():
        if key in lowered:
            return factor, lead_days, notes.get(language, notes["en"])
    return 1.00, 25, ""


def eta_reply_text(po_name: str, new_date: date, language: str = "en") -> str:
    if language == "es":
        written = f"{new_date.day} de {MONTHS_ES[new_date.month - 1]} de {new_date.year}"
        return (
            "Estimado equipo de Compras:\n\n"
            f"Lamentamos la demora con la orden de compra {po_name}.\n\n"
            f"Nueva fecha de entrega confirmada en su almacén: {written} "
            f"({new_date.isoformat()}), para todas las líneas de la orden.\n\n"
            "Saludos cordiales,\nVentas\nProveedor Hidraulica"
        )
    return (
        "Dear Purchasing Team,\n\n"
        f"We apologise for the delay on purchase order {po_name}.\n\n"
        f"New confirmed delivery date at your warehouse: {new_date.strftime('%d %B %Y')} "
        f"({new_date.isoformat()}), for every line of the order.\n\n"
        "Kind regards,\nSales\nProveedor Hidraulica"
    )


def quote_text(
    po_name: str,
    lines: list[dict[str, Any]],
    *,
    lead_days: int,
    supplier: str,
    note: str = "",
    language: str = "en",
) -> str:
    extra = f"{note}\n\n" if note else ""
    if language == "es":
        rows = "\n".join(
            f"- {line['product']}: {line['qty']:g} unidades a USD {line['price']:.2f} cada una"
            for line in lines
        )
        return (
            "Estimado equipo de Compras:\n\n"
            f"Gracias por su solicitud de cotización {po_name}. Nuestra cotización:\n{rows}\n\n"
            f"Plazo de entrega: {lead_days} días. Precios en USD, sin impuestos. "
            f"Validez: 15 días.\n\n{extra}Saludos cordiales,\nVentas\n{supplier}"
        )
    rows = "\n".join(
        f"- {line['product']}: {line['qty']:g} units at USD {line['price']:.2f} each"
        for line in lines
    )
    return (
        "Dear Purchasing Team,\n\n"
        f"Thank you for your request for quotation {po_name}. Our quote:\n{rows}\n\n"
        f"Delivery time: {lead_days} days. Prices in USD, taxes not included. Valid for 15 days."
        f"\n\n{extra}Kind regards,\nSales\n{supplier}"
    )


def acceptance_text(po_name: str, offered: float | None, language: str = "en") -> str:
    if language == "es":
        price = f" de USD {offered:.2f} por unidad" if offered else ""
        return (
            "Estimado equipo de Compras:\n\n"
            f"Aceptamos su contraoferta{price} para la solicitud {po_name}. "
            "Se mantiene el plazo de entrega de 20 días.\n\n"
            "Quedamos atentos a su orden de compra.\n\nSaludos cordiales,\nVentas\n"
            "Proveedor Hidraulica"
        )
    price = f" of USD {offered:.2f} per unit" if offered else ""
    return (
        "Dear Purchasing Team,\n\n"
        f"We accept your counter-offer{price} for request {po_name}. "
        "The delivery time of 20 days stands.\n\n"
        "We look forward to your purchase order.\n\nKind regards,\nSales\n"
        "Proveedor Hidraulica"
    )


class SupplierEmail(StrictModel):
    """One email the demo sent for a supplier, rebuilt for the presenter to read."""

    step: str
    po_name: str
    from_name: str
    from_email: str
    subject: str
    text: str


# --- the script -----------------------------------------------------------------------


class DemoStep(StrictModel):
    key: str
    title: str
    say: str = Field(description="what the presenter says while it runs")
    click: str = Field(description="where the audience looks in the Control Tower")
    needs: StepNeeds = "none"


STEPS: list[DemoStep] = [
    DemoStep(
        key="late_order_eta",
        title="A late order gets a delivery-date request",
        say="Proveedor Hidraulica is late on an order. Nobody noticed; the department did. "
        "It writes to the supplier asking for a firm date, in the supplier's language.",
        click="Approvals: the email waits for a person unless a rule lets it go alone.",
        needs="none",
    ),
    DemoStep(
        key="supplier_eta_reply",
        title="The supplier answers with a new date",
        say="The supplier replies from its own mailbox. The agent reads the date and "
        "proposes to move the order; the order itself only changes after a decision.",
        click="Approvals: the order change with the new date; Board: the card moves.",
        needs="mailbox",
    ),
    DemoStep(
        key="risk_quote_round",
        title="A stockout risk starts a quote round",
        say="The risk radar sees a product likely to run out inside its lead time. One click "
        "asks every supplier who lists it for a quote.",
        click="Risk: the product at the top; Board: one RFQ per supplier, grouped.",
        needs="none",
    ),
    DemoStep(
        key="supplier_quote",
        title="A quote above target gets a counter-offer",
        say="The supplier quotes above what we last paid. The sourcing agent proposes a "
        "counter-offer within the buyer's cap; a person approves it before it goes.",
        click="Approvals: the counter-offer with the evidence behind the target price.",
        needs="mailbox",
    ),
    DemoStep(
        key="award",
        title="The supplier accepts and the round is awarded",
        say="The supplier accepts. The comparison weighs landed cost, lead time and score, "
        "recommends, and a person awards. Odoo confirms the order and it goes out.",
        click="Approvals: the award; Board: the order confirmed.",
        needs="mailbox",
    ),
    DemoStep(
        key="short_receipt",
        title="A short receipt gets a discrepancy report",
        say="The warehouse receives fewer units than ordered. The logistics agent "
        "reconciles the receipt and drafts the discrepancy for the supplier.",
        click="Board: the receipt card; Approvals: the discrepancy email.",
        needs="world",
    ),
    DemoStep(
        key="invoice_variance",
        title="An invoice with a price variance",
        say="Accounting types the supplier's bill; one line is three percent above the "
        "order. The matching agent finds it and asks before anything is posted.",
        click="Approvals: the vendor bill with the variance per line.",
        needs="world",
    ),
    DemoStep(
        key="briefing",
        title="The briefing the next morning",
        say="Every morning the Director agent writes what happened, what ran alone and what "
        "needs a decision, with the facts behind each line.",
        click="Briefing: today's paragraph and sections; Home: the day at a glance.",
        needs="none",
    ),
]

STEP_KEYS = [step.key for step in STEPS]


class DemoLink(StrictModel):
    label: str
    path: str


class DemoStepOutcome(StrictModel):
    key: str
    status: StepStatus
    summary: str
    links: list[DemoLink] = Field(default_factory=list)
    approval_ids: list[int] = Field(default_factory=list)
    at: datetime = Field(default_factory=utc_now)


class DemoState(StrictModel):
    position: int = 0  # steps completed; the next one is STEPS[position]
    started_at: datetime | None = None
    records: dict[str, Any] = Field(default_factory=dict)
    outcomes: list[DemoStepOutcome] = Field(default_factory=list)
    approval_ids: list[int] = Field(default_factory=list)  # what the run created
    updated_at: datetime = Field(default_factory=utc_now)


class DemoReadiness(StrictModel):
    mailbox: bool
    world: bool
    notes: list[str] = Field(default_factory=list)


class DemoView(StrictModel):
    steps: list[DemoStep]
    position: int
    started_at: datetime | None
    finished: bool
    next: DemoStep | None
    outcomes: list[DemoStepOutcome]
    records: dict[str, Any]
    ready: DemoReadiness


class DemoActor(StrictModel):
    name: str
    email: str


# --- stores --------------------------------------------------------------------------


@runtime_checkable
class DemoStore(Protocol):
    async def load(self) -> DemoState: ...

    async def save(self, state: DemoState) -> DemoState: ...


class PostgresDemoStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def load(self) -> DemoState:
        row = await self._db.fetch_one("SELECT * FROM demo_state WHERE id = 1")
        if row is None:
            return DemoState()
        return DemoState(
            position=int(row["position"]),
            started_at=row["started_at"],
            records=row["records"] or {},
            outcomes=[DemoStepOutcome.model_validate(o) for o in (row["outcomes"] or [])],
            approval_ids=list(row["approval_ids"] or []),
            updated_at=row["updated_at"],
        )

    async def save(self, state: DemoState) -> DemoState:
        state = state.model_copy(update={"updated_at": utc_now()})
        await self._db.execute(
            "INSERT INTO demo_state (id, position, started_at, records, outcomes, approval_ids, "
            "updated_at) VALUES (1, %s, %s, %s::jsonb, %s::jsonb, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET position = EXCLUDED.position, "
            "started_at = EXCLUDED.started_at, records = EXCLUDED.records, "
            "outcomes = EXCLUDED.outcomes, approval_ids = EXCLUDED.approval_ids, "
            "updated_at = EXCLUDED.updated_at",
            (
                state.position,
                state.started_at,
                json.dumps(state.records, default=str),
                json.dumps([o.model_dump(mode="json") for o in state.outcomes]),
                state.approval_ids,
                state.updated_at,
            ),
        )
        return state


class MemoryDemoStore:
    def __init__(self) -> None:
        self.state = DemoState()

    async def load(self) -> DemoState:
        return self.state.model_copy(deep=True)

    async def save(self, state: DemoState) -> DemoState:
        self.state = state.model_copy(update={"updated_at": utc_now()}, deep=True)
        return self.state


# --- the world outside the department ------------------------------------------------


class DemoOrder(StrictModel):
    po_id: int
    po_name: str
    partner_id: int
    date_planned: date | None = None
    amount_total: float = 0.0


class DemoOrderLine(StrictModel):
    line_id: int
    product_id: int
    product: str
    qty: float
    price_unit: float
    qty_received: float = 0.0


class DemoReceipt(StrictModel):
    picking_name: str
    expected: float
    received: float


class DemoBill(StrictModel):
    move_id: int
    ref: str
    amount: float


@runtime_checkable
class DemoWorld(Protocol):
    """Odoo as the supplier's counterpart, the warehouse and accounting would use it."""

    @property
    def can_receive(self) -> bool: ...

    async def late_order(self, supplier_email: str) -> DemoOrder | None: ...

    async def set_planned_date(self, po_id: int, when: date) -> None: ...

    async def create_confirmed_order(
        self, supplier_email: str, *, external_ref: str
    ) -> DemoOrder: ...

    async def order_lines(self, po_name: str) -> list[DemoOrderLine]: ...

    async def receive_short(self, po_id: int, *, fraction: float) -> DemoReceipt: ...

    async def type_bill(self, po_id: int, *, ref: str, uplift_pct: float) -> DemoBill: ...

    async def cancel_orders(self, po_names: list[str]) -> int: ...

    async def outbound_count(self, po_name: str) -> int:
        """How many emails of ours are linked to the order (they are linked once sent)."""
        ...

    async def inbound_count(self, po_name: str) -> int:
        """How many supplier emails are linked to the order."""
        ...

    async def supplier_counts(self, product_ids: list[int]) -> dict[int, int]:
        """How many suppliers list each product (a round needs rivals to compare)."""
        ...

    async def supplier_contact(self, po_name: str) -> tuple[str, str | None]:
        """The order's supplier: name and mailbox."""
        ...


QUIET = {"sc_skip_events": True}  # the addon's rules stay silent for demo housekeeping


class OdooDemoWorld:
    """The bot reads and writes orders; a second login (stock and accounting rights) plays
    the warehouse and accounting. Without it, those two steps report what is missing."""

    def __init__(
        self,
        bot: OdooClient,
        admin: OdooClient | None,
        *,
        today: Callable[[], date] = local_today,
    ) -> None:
        self._bot = bot
        self._admin = admin
        self._today = today

    @property
    def can_receive(self) -> bool:
        return self._admin is not None

    async def _partner_id(self, email: str) -> int:
        partners = PartnerRepo(self._bot)
        partner = await partners.find_by_email(email)
        if partner is None:
            raise ScError(f"no supplier with the mailbox {email} in Odoo")
        return (await partners.commercial_partner(partner)).id

    async def late_order(self, supplier_email: str) -> DemoOrder | None:
        partner_id = await self._partner_id(supplier_email)
        orders = PurchaseOrderRepo(self._bot)
        late = [
            po
            for po in await orders.late_open_orders(as_of=self._today())
            if po.partner_id.id == partner_id and po.date_planned is not None
        ]
        if not late:
            return None
        po = min(late, key=lambda p: p.date_planned or datetime.max)
        return DemoOrder(
            po_id=po.id,
            po_name=po.name,
            partner_id=partner_id,
            date_planned=po.date_planned.date() if po.date_planned else None,
            amount_total=po.amount_total,
        )

    async def set_planned_date(self, po_id: int, when: date) -> None:
        orders = PurchaseOrderRepo(self._bot)
        stamp = datetime.combine(when, datetime.min.time()).replace(hour=12)
        for line in await orders.lines(po_id):
            await orders.set_line_date_planned(
                line.id, stamp, source="supplier", run_id="demo_reset"
            )

    async def create_confirmed_order(self, supplier_email: str, *, external_ref: str) -> DemoOrder:
        partner_id = await self._partner_id(supplier_email)
        orders = PurchaseOrderRepo(self._bot)
        prices = SupplierInfoRepo(self._bot)
        infos = await prices.for_partner(partner_id)
        templates = [
            i.product_tmpl_id.id for i in infos if i.product_id is None and i.product_tmpl_id
        ]
        variants = await prices.variant_ids(templates[:6]) if templates else {}
        product_ids: list[int] = []
        for info in infos:
            pid = (
                info.product_id.id
                if info.product_id
                else (variants.get(info.product_tmpl_id.id) if info.product_tmpl_id else None)
            )
            if pid and pid not in product_ids:
                product_ids.append(pid)
            if len(product_ids) == 2:
                break
        if not product_ids:
            raise ScError("the demo supplier lists no product; seed the dataset first")
        rfq = await orders.create_rfq(
            partner_id,
            [NewOrderLine(product_id=pid, product_qty=10.0) for pid in product_ids],
            external_ref=external_ref,
            origin="demo",
        )
        if rfq.state in ("draft", "sent"):
            # quiet: the confirmation is housekeeping, not something the department did
            await self._bot.call("purchase.order", "button_confirm", [rfq.id], context=QUIET)
        po = await orders.get(rfq.id)
        return DemoOrder(
            po_id=po.id,
            po_name=po.name,
            partner_id=partner_id,
            date_planned=po.date_planned.date() if po.date_planned else None,
            amount_total=po.amount_total,
        )

    async def order_lines(self, po_name: str) -> list[DemoOrderLine]:
        orders = PurchaseOrderRepo(self._bot)
        po = await orders.get_by_name(po_name)
        if po is None:
            return []
        return [
            DemoOrderLine(
                line_id=line.id,
                product_id=line.product_id.id,
                product=line.product_id.name or str(line.product_id.id),
                qty=line.product_qty,
                price_unit=line.price_unit,
                qty_received=line.qty_received,
            )
            for line in await orders.lines(po.id)
            if line.product_id is not None
        ]

    async def receive_short(self, po_id: int, *, fraction: float) -> DemoReceipt:
        if self._admin is None:
            raise ScError("SC__DEMO__ODOO_LOGIN / ODOO_API_KEY not set: nobody can receive")
        pickings = PickingRepo(self._admin)
        open_pickings = [
            p for p in await pickings.incoming_for_po(po_id) if p.state not in ("done", "cancel")
        ]
        if not open_pickings:
            raise ScError("the demo order has no receipt left to validate")
        picking = open_pickings[0]
        moves = await pickings.moves(picking.id)
        expected = sum(m.product_uom_qty for m in moves)
        received = 0.0
        for index, move in enumerate(moves):
            qty = (
                math.floor(move.product_uom_qty * fraction) if index == 0 else move.product_uom_qty
            )
            qty = max(1.0, float(qty)) if move.product_uom_qty else 0.0
            await self._admin.write("stock.move", [move.id], {"quantity": qty, "picked": True})
            received += qty
        # No backorder: what is missing is a discrepancy, not a delivery still to come.
        await self._admin.call(
            "stock.picking",
            "button_validate",
            [picking.id],
            context={"skip_backorder": True, "picking_ids_not_to_backorder": [picking.id]},
        )
        return DemoReceipt(picking_name=picking.name, expected=expected, received=received)

    async def type_bill(self, po_id: int, *, ref: str, uplift_pct: float) -> DemoBill:
        if self._admin is None:
            raise ScError("SC__DEMO__ODOO_LOGIN / ODOO_API_KEY not set: nobody can type the bill")
        orders = PurchaseOrderRepo(self._bot)
        po = await orders.get(po_id)
        lines = await orders.lines(po_id)
        invoice_lines: list[Any] = []
        amount = 0.0
        for index, line in enumerate(lines):
            qty = line.qty_received or line.product_qty
            if not qty or line.product_id is None:
                continue
            price = (
                round(line.price_unit * (1 + uplift_pct / 100), 2)
                if index == 0
                else line.price_unit
            )
            amount += qty * price
            invoice_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "name": line.product_id.name or "",
                        "quantity": qty,
                        "price_unit": price,
                        "purchase_line_id": line.id,
                    },
                )
            )
        if not invoice_lines:
            raise ScError("nothing received on the demo order: receive it first")
        # One create so the addon's on-create rule sees the final prices.
        move_id = await self._admin.create(
            "account.move",
            {
                "move_type": "in_invoice",
                "partner_id": po.partner_id.id,
                "ref": ref,
                "invoice_date": local_today().isoformat(),
                "invoice_origin": po.name,
                "invoice_line_ids": invoice_lines,
            },
        )
        return DemoBill(move_id=int(move_id), ref=ref, amount=round(amount, 2))

    async def _mail_count(self, po_name: str, direction: str) -> int:
        po = await PurchaseOrderRepo(self._bot).get_by_name(po_name)
        if po is None:
            return 0
        links = await MailLinkRepo(self._bot).for_po(po.id)
        return sum(1 for link in links if link.direction == direction)

    async def outbound_count(self, po_name: str) -> int:
        return await self._mail_count(po_name, "out")

    async def inbound_count(self, po_name: str) -> int:
        return await self._mail_count(po_name, "in")

    async def supplier_counts(self, product_ids: list[int]) -> dict[int, int]:
        prices = SupplierInfoRepo(self._bot)
        counts: dict[int, int] = {}
        for product_id in product_ids:
            entries = await prices.for_product(product_id)
            counts[product_id] = len({entry.partner_id.id for entry in entries})
        return counts

    async def supplier_contact(self, po_name: str) -> tuple[str, str | None]:
        po = await PurchaseOrderRepo(self._bot).get_by_name(po_name)
        if po is None:
            return po_name, None
        emails = await PartnerRepo(self._bot).emails_of(po.partner_id.id)
        return po.partner_id.name or po_name, (emails[0] if emails else None)

    async def cancel_orders(self, po_names: list[str]) -> int:
        orders = PurchaseOrderRepo(self._bot)
        cancelled = 0
        for po in await orders.by_names(po_names):
            if po.state in ("draft", "sent"):
                try:
                    await orders.cancel(po.id)
                    cancelled += 1
                except ScError as exc:
                    logger.warning("demo reset could not cancel {}: {}", po.name, exc)
        return cancelled


class MemoryDemoWorld:
    """A world of dicts for tests: orders by name, receipts and bills as they happen."""

    def __init__(self) -> None:
        self.can_receive = True
        self.late: DemoOrder | None = None
        self.planned: dict[int, date] = {}
        self.created: list[DemoOrder] = []
        self.lines: dict[str, list[DemoOrderLine]] = {}
        self.receipts: list[tuple[int, float]] = []
        self.bills: list[tuple[int, str, float]] = []
        self.cancelled: list[str] = []
        self.outbound: dict[str, int] = {}  # emails of ours per order
        self.inbound: dict[str, int] = {}  # supplier emails per order
        self.listed: dict[int, int] = {}  # suppliers listing a product
        self.contacts: dict[str, tuple[str, str | None]] = {}  # order -> supplier
        self._next_id = 900

    async def late_order(self, supplier_email: str) -> DemoOrder | None:
        return self.late

    async def set_planned_date(self, po_id: int, when: date) -> None:
        self.planned[po_id] = when

    async def create_confirmed_order(self, supplier_email: str, *, external_ref: str) -> DemoOrder:
        self._next_id += 1
        order = DemoOrder(
            po_id=self._next_id, po_name=f"P{self._next_id:05d}", partner_id=8, amount_total=250.0
        )
        self.created.append(order)
        self.lines[order.po_name] = [
            DemoOrderLine(line_id=1, product_id=1, product="Valvula", qty=10, price_unit=25.0)
        ]
        return order

    async def order_lines(self, po_name: str) -> list[DemoOrderLine]:
        return list(self.lines.get(po_name, []))

    async def receive_short(self, po_id: int, *, fraction: float) -> DemoReceipt:
        if not self.can_receive:
            raise ScError("SC__DEMO__ODOO_LOGIN / ODOO_API_KEY not set: nobody can receive")
        self.receipts.append((po_id, fraction))
        return DemoReceipt(picking_name=f"WH/IN/{po_id}", expected=10, received=8)

    async def type_bill(self, po_id: int, *, ref: str, uplift_pct: float) -> DemoBill:
        if not self.can_receive:
            raise ScError("SC__DEMO__ODOO_LOGIN / ODOO_API_KEY not set: nobody can type the bill")
        self.bills.append((po_id, ref, uplift_pct))
        return DemoBill(move_id=500 + len(self.bills), ref=ref, amount=206.0)

    async def outbound_count(self, po_name: str) -> int:
        return self.outbound.get(po_name, 0)

    async def inbound_count(self, po_name: str) -> int:
        return self.inbound.get(po_name, 0)

    async def supplier_counts(self, product_ids: list[int]) -> dict[int, int]:
        return {pid: self.listed.get(pid, 1) for pid in product_ids}

    async def supplier_contact(self, po_name: str) -> tuple[str, str | None]:
        return self.contacts.get(
            po_name, ("Proveedor Hidraulica", "ventas.hidraulica.sc@gmail.com")
        )

    async def cancel_orders(self, po_names: list[str]) -> int:
        self.cancelled.extend(po_names)
        return len(po_names)


# --- the supplier's mailbox ------------------------------------------------------------


@runtime_checkable
class SupplierMailbox(Protocol):
    async def reply(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        sender_name: str | None = None,
        sender_email: str | None = None,
    ) -> str:
        """Send from the supplier's mailbox; returns the Message-ID."""
        ...


class SmtpSupplierMailbox:
    """The demo supplier writes back from its own mailbox (SMTP with an app password)."""

    def __init__(self, cfg: DemoCfg, *, supplier_name: str = "Proveedor Hidraulica") -> None:
        self._cfg = cfg
        self._name = supplier_name

    async def reply(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        sender_name: str | None = None,
        sender_email: str | None = None,
    ) -> str:
        message = EmailMessage()
        # the rivals are aliases of the same mailbox (name+alias@...): one login sends for all
        message["From"] = (
            f"{sender_name or self._name} <{sender_email or self._cfg.supplier_email}>"
        )
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self._cfg.supplier_email.split("@")[-1])
        message.set_content(text)
        cfg = self._cfg

        def send() -> None:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(cfg.smtp_user, cfg.smtp_password.get_secret_value())
                smtp.send_message(message)

        try:
            await asyncio.to_thread(send)
        except (OSError, smtplib.SMTPException) as exc:
            raise ScError(f"the supplier's mailbox did not send: {exc}") from exc
        return str(message["Message-ID"])


class MemorySupplierMailbox:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def reply(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        sender_name: str | None = None,
        sender_email: str | None = None,
    ) -> str:
        self.sent.append(
            {
                "to": to,
                "subject": subject,
                "text": text,
                "from": sender_name or "Proveedor Hidraulica",
            }
        )
        return f"<demo-{len(self.sent)}@test>"


# --- approving on the presenter's behalf ---------------------------------------------


@runtime_checkable
class ApprovalResolver(Protocol):
    async def approve(self, approval_id: int, *, actor: DemoActor) -> None: ...


Sleep = Callable[[float], Awaitable[None]]


# --- the runner ----------------------------------------------------------------------


class DemoDirector:
    """Runs the script one step at a time and remembers where it stands."""

    def __init__(
        self,
        *,
        cfg: DemoCfg,
        store: DemoStore,
        world: DemoWorld,
        mailbox: SupplierMailbox | None,
        deps: Deps,
        approvals: ApprovalsGateway,
        cases: CaseStore,
        mailbox_sync: MailboxSync,
        risk: RiskSource,
        sourcing: SourcingDispatcher,
        sourcing_source: SourcingSource,
        briefing: BriefingJob,
        resolver: ApprovalResolver,
        sleep: Sleep = asyncio.sleep,
        wait_seconds: int | None = None,
        poll_seconds: float | None = None,
        chain_wait_seconds: int = 20,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._world = world
        self._mailbox = mailbox
        self._deps = deps
        self._approvals = approvals
        self._cases = cases
        self._sync = mailbox_sync
        self._risk = risk
        self._sourcing = sourcing
        self._rounds = sourcing_source
        self._briefing = briefing
        self._resolver = resolver
        self._sleep = sleep
        self._wait = cfg.wait_seconds if wait_seconds is None else wait_seconds
        self._poll = cfg.poll_seconds if poll_seconds is None else poll_seconds
        # After a decision, what it triggers (an order going out) shows up within seconds.
        self._chain_wait = min(chain_wait_seconds, self._wait)
        self._lock = asyncio.Lock()
        self._leave: frozenset[str] = frozenset()

    # -- reading ------------------------------------------------------------------------

    def readiness(self) -> DemoReadiness:
        notes: list[str] = []
        mailbox = self._mailbox is not None
        if not mailbox:
            notes.append(
                "SC__DEMO__SMTP_USER / SMTP_PASSWORD not set: the supplier's replies "
                "(steps 2, 4 and 5) cannot be sent"
            )
        world = self._world.can_receive
        if not world:
            notes.append(
                "SC__DEMO__ODOO_LOGIN / ODOO_API_KEY not set: the receipt and the bill "
                "(steps 6 and 7) cannot be played"
            )
        return DemoReadiness(mailbox=mailbox, world=world, notes=notes)

    async def view(self) -> DemoView:
        state = await self._store.load()
        return self._view(state)

    def _view(self, state: DemoState) -> DemoView:
        finished = state.position >= len(STEPS)
        return DemoView(
            steps=STEPS,
            position=state.position,
            started_at=state.started_at,
            finished=finished,
            next=None if finished else STEPS[state.position],
            outcomes=state.outcomes,
            records=state.records,
            ready=self.readiness(),
        )

    async def supplier_emails(self) -> list[SupplierEmail]:
        """What the demo wrote for the suppliers in this run, rebuilt from the records."""
        records = (await self._store.load()).records
        bot = self._cfg.supplier_email
        lang = self._cfg.language
        emails: list[SupplierEmail] = []
        late = records.get("late_order") or {}
        if records.get("eta_reply_sent") and records.get("eta_new_date"):
            emails.append(
                SupplierEmail(
                    step="supplier_eta_reply",
                    po_name=str(late.get("po_name")),
                    from_name="Proveedor Hidraulica",
                    from_email=bot,
                    subject=subject_for("eta", str(late.get("po_name")), lang),
                    text=eta_reply_text(
                        str(late.get("po_name")), date.fromisoformat(records["eta_new_date"]), lang
                    ),
                )
            )
        for po_name, quote in (records.get("quotes") or {}).items():
            emails.append(
                SupplierEmail(
                    step="supplier_quote",
                    po_name=po_name,
                    from_name=quote["supplier"],
                    from_email=quote["email"],
                    subject=subject_for("quote", po_name, lang),
                    text=quote_text(
                        po_name,
                        quote["lines"],
                        lead_days=quote["lead_days"],
                        supplier=quote["supplier"],
                        note=quote["note"],
                        language=lang,
                    ),
                )
            )
        if records.get("acceptance_sent"):
            po_name = str(records.get("supplier_rfq"))
            emails.append(
                SupplierEmail(
                    step="award",
                    po_name=po_name,
                    from_name="Proveedor Hidraulica",
                    from_email=bot,
                    subject=subject_for("quote", po_name, lang),
                    text=acceptance_text(po_name, records.get("accepted_price"), lang),
                )
            )
        return emails

    # -- reset --------------------------------------------------------------------------

    async def reset(self, actor: DemoActor) -> DemoView:
        """Back to the start: the late order gets its original date, the round's RFQs are
        cancelled, the run's pending approvals are rejected, and a fresh order is confirmed
        for the receipt and the bill (a received order cannot be un-received)."""
        async with self._lock:
            state = await self._store.load()
            records = dict(state.records)
            notes: list[str] = []
            rfq_names = list(records.get("rfq_names") or [])
            if rfq_names:
                cancelled = await self._world.cancel_orders(rfq_names)
                notes.append(f"{cancelled} RFQ(s) of the last round cancelled")
            for approval_id in state.approval_ids:
                try:
                    approval = await self._approvals.get(approval_id)
                    if approval.status == "pending":
                        await self._approvals.resolve(
                            approval_id,
                            "rejected",
                            by_name=actor.name,
                            reason="demo reset",
                            details=None,
                        )
                except ScError as exc:
                    logger.warning("demo reset left approval {}: {}", approval_id, exc)
            late = records.get("late_order")
            if late and late.get("original_date_planned"):
                await self._world.set_planned_date(
                    int(late["po_id"]), date.fromisoformat(late["original_date_planned"])
                )
            fresh = await self._world.late_order(self._cfg.supplier_email)
            new_records: dict[str, Any] = {}
            if fresh is not None:
                original = (
                    (late or {}).get("original_date_planned")
                    if late and late.get("po_id") == fresh.po_id
                    else None
                )
                new_records["late_order"] = {
                    "po_id": fresh.po_id,
                    "po_name": fresh.po_name,
                    "partner_id": fresh.partner_id,
                    "original_date_planned": original
                    or (fresh.date_planned.isoformat() if fresh.date_planned else None),
                }
            else:
                notes.append("no late order for the demo supplier: step 1 will say so")
            receipt = await self._world.create_confirmed_order(
                self._cfg.supplier_email, external_ref=f"demo-receipt-{new_id('d')}"
            )
            new_records["receipt_order"] = receipt.model_dump(mode="json")
            new_records["reset_notes"] = notes
            # What was pending before the run: anything newer is the run's own.
            new_records["baseline_approval_ids"] = sorted(await self._pending_ids(None))
            state = DemoState(
                position=0,
                started_at=utc_now(),
                records=new_records,
                outcomes=[],
                approval_ids=[],
            )
            state = await self._store.save(state)
            logger.bind(by=actor.email).info("demo reset {}", "; ".join(notes) or "clean")
            return self._view(state)

    # -- stepping -----------------------------------------------------------------------

    async def next(
        self, actor: DemoActor, *, approve: bool, leave: frozenset[str] = frozenset()
    ) -> DemoView:
        state = await self._store.load()
        if state.position >= len(STEPS):
            return self._view(state)
        return await self.run(STEPS[state.position].key, actor, approve=approve, leave=leave)

    async def run(
        self, key: str, actor: DemoActor, *, approve: bool, leave: frozenset[str] = frozenset()
    ) -> DemoView:
        """Run one step (the next one, or an earlier one again); a finished step moves
        the position forward only when it is the next one."""
        if key not in STEP_KEYS:
            raise ScError(f"no demo step named {key!r}")
        async with self._lock:
            state = await self._store.load()
            if state.started_at is None:
                raise ScError("the demo has not been reset yet")
            runner = getattr(self, f"_step_{key}")
            # kinds the presenter wants to decide on screen even when the rest is automatic
            self._leave = leave
            try:
                outcome = await runner(state, actor, approve)
            except ScError as exc:
                outcome = DemoStepOutcome(key=key, status="failed", summary=exc.message)
            except Exception as exc:  # noqa: BLE001 - the page must say what broke
                logger.exception("demo step {} broke", key)
                outcome = DemoStepOutcome(key=key, status="failed", summary=str(exc))
            approval_ids = list(state.approval_ids) + [
                i for i in outcome.approval_ids if i not in state.approval_ids
            ]
            position = state.position
            if outcome.status == "done" and STEP_KEYS.index(key) == position:
                position += 1
            state = await self._store.save(
                state.model_copy(
                    update={
                        "approval_ids": approval_ids,
                        "outcomes": [o for o in state.outcomes if o.key != key] + [outcome],
                        "position": position,
                    }
                )
            )
            logger.bind(step=key, status=outcome.status, by=actor.email).info(
                "demo step {}", outcome.summary
            )
            return self._view(state)

    # -- helpers ------------------------------------------------------------------------

    @staticmethod
    def _seen(state: DemoState) -> set[int]:
        """Approvals that are not news: pending before the reset, or already this run's."""
        return set(state.approval_ids) | set(state.records.get("baseline_approval_ids") or [])

    async def _pending_ids(self, po_name: str | None) -> set[int]:
        rows = await self._approvals.list(status="pending", kind=None, po_name=po_name)
        return {row.id for row in rows}

    async def _wait_for(
        self, check: Callable[[], Awaitable[bool]], *, wait: int | None = None
    ) -> bool:
        """Poll until ``check`` says yes or the step's patience runs out."""
        patience = self._wait if wait is None else wait
        attempts = max(1, int(patience / self._poll)) if patience else 1
        for attempt in range(attempts):
            if await check():
                return True
            if attempt + 1 < attempts:
                await self._sleep(self._poll)
        return False

    async def _wait_outbound(self, po_name: str, *, more_than: int) -> bool:
        """The supplier can only answer an email that reached it: wait until ours left."""

        async def check() -> bool:
            return await self._world.outbound_count(po_name) > more_than

        return await self._wait_for(check)

    async def _mark_inbound(self, state: DemoState, po_name: str) -> None:
        """Remember how many supplier emails the order had before the demo sends one."""
        before = dict(state.records.get("in_before") or {})
        before[po_name] = await self._world.inbound_count(po_name)
        state.records["in_before"] = before

    async def _sync_until_linked(self, po_name: str, state: DemoState | None = None) -> bool:
        """Read the mailbox until the supplier's email is linked to the order, whoever
        linked it: this sync, or the scheduler's a moment earlier."""
        before = int(((state.records.get("in_before") or {}) if state else {}).get(po_name, -1))

        async def check() -> bool:
            report = await self._sync.run(requested_by="demo")
            if po_name in (report.get("linked_po_names") or []):
                return True
            return before >= 0 and await self._world.inbound_count(po_name) > before

        return await self._wait_for(check)

    async def _wait_for_approvals(
        self,
        po_name: str,
        *,
        seen: set[int],
        kinds: set[str] | None = None,
        wait: int | None = None,
    ) -> list[int]:
        found: list[int] = []

        async def check() -> bool:
            rows = await self._approvals.list(status="pending", kind=None, po_name=po_name)
            found[:] = [r.id for r in rows if r.id not in seen and (not kinds or r.kind in kinds)]
            return bool(found)

        await self._wait_for(check, wait=wait)
        return found

    async def _approve_chain(
        self, po_name: str, approval_ids: list[int], *, actor: DemoActor, rounds: int = 3
    ) -> list[int]:
        """Approve what a step created and whatever those approvals trigger next (an
        awarded RFQ becomes an order that goes out, a counter-offer becomes an email)."""
        approved: list[int] = []
        seen = await self._pending_ids(po_name)
        pending = list(approval_ids)
        for _ in range(rounds):
            if not pending:
                break
            for approval_id in pending:
                if self._leave and (await self._approvals.get(approval_id)).kind in self._leave:
                    approved.append(approval_id)  # the run's own, left for a person
                    continue
                await self._resolver.approve(approval_id, actor=actor)
                approved.append(approval_id)
            seen |= set(pending)
            pending = await self._wait_for_approvals(po_name, seen=seen, wait=self._chain_wait)
        return approved

    @staticmethod
    def _links(po_name: str | None, approval_ids: list[int], *extra: DemoLink) -> list[DemoLink]:
        links: list[DemoLink] = []
        for approval_id in approval_ids:
            links.append(
                DemoLink(label=f"approval #{approval_id}", path=f"/approvals?id={approval_id}")
            )
        if po_name:
            links.append(DemoLink(label=po_name, path=f"/board?po={po_name}"))
        links.extend(extra)
        return links

    async def _supplier_task(
        self, kind: str, po_name: str, partner_id: int | None, *, notes: str
    ) -> tuple[str, int | None, str]:
        """One supplier_comms task on the order's case, consolidated like any other run."""
        case, _ = await self._cases.attach_or_create(
            kind="eta", po_name=po_name, partner_id=partner_id, agent="supplier_comms"
        )
        thread_id = f"demo_{new_id('t')}"
        task = SupplierCommsTask(kind=kind, case_id=thread_id, po_name=po_name, notes=notes)  # type: ignore[arg-type]
        await self._cases.add_event(
            case.case_id,
            "task_sent",
            {
                "agent": "supplier_comms",
                "task": kind,
                "thread_id": thread_id,
                "po_name": po_name,
                "by": "demo",
            },
        )
        try:
            reply = await self._deps.agents.for_name("supplier_comms").send(
                task.model_dump_json(), case_id=case.case_id
            )
        except ScError as exc:
            outcome = outcome_from_reply(case, "supplier_comms", kind, thread_id, None, error=exc)
        else:
            outcome = outcome_from_reply(case, "supplier_comms", kind, thread_id, reply)
        await consolidate_outcome(
            self._cases, self._deps.escalator, self._deps.conversations, outcome
        )
        return outcome.status, outcome.approval_id, outcome.summary

    def _need_mailbox(self) -> SupplierMailbox:
        if self._mailbox is None:
            raise ScError(
                "the supplier's mailbox is not configured (SC__DEMO__SMTP_USER / SMTP_PASSWORD)"
            )
        return self._mailbox

    # -- the steps ----------------------------------------------------------------------

    async def _step_late_order_eta(
        self, state: DemoState, actor: DemoActor, approve: bool
    ) -> DemoStepOutcome:
        late = state.records.get("late_order")
        if not late:
            raise ScError("no late order for the demo supplier; seed the dataset and reset")
        po_name = str(late["po_name"])
        status, approval_id, summary = await self._supplier_task(
            "request_eta",
            po_name,
            int(late["partner_id"]),
            notes="The order is past its planned date; ask for a firm delivery date.",
        )
        ids = [approval_id] if approval_id else []
        if status == "awaiting_approval" and approve and approval_id:
            ids = await self._approve_chain(po_name, [approval_id], actor=actor)
            summary = f"{summary}; approved by {actor.name}, the email is on its way"
        elif status not in ("sent", "awaiting_approval"):
            return DemoStepOutcome(key="late_order_eta", status="failed", summary=summary)
        return DemoStepOutcome(
            key="late_order_eta",
            status="done",
            summary=summary,
            links=self._links(po_name, ids),
            approval_ids=ids,
        )

    async def _step_supplier_eta_reply(
        self, state: DemoState, actor: DemoActor, approve: bool
    ) -> DemoStepOutcome:
        late = state.records.get("late_order")
        if not late:
            raise ScError("no late order in this run; reset first")
        po_name = str(late["po_name"])
        seen = self._seen(state)
        if not state.records.get("eta_reply_sent"):
            mailbox = self._need_mailbox()
            new_date = local_today() + timedelta(days=7)
            await self._mark_inbound(state, po_name)
            message_id = await mailbox.reply(
                to=self._cfg.bot_email,
                subject=subject_for("eta", po_name, self._cfg.language),
                text=eta_reply_text(po_name, new_date, self._cfg.language),
            )
            state.records["eta_reply_sent"] = message_id
            state.records["eta_new_date"] = new_date.isoformat()
            await self._store.save(state)
        if not await self._sync_until_linked(po_name, state):
            return DemoStepOutcome(
                key="supplier_eta_reply",
                status="waiting",
                summary=f"the reply has not reached the mailbox yet; run the step again "
                f"(sent as {state.records['eta_reply_sent']})",
            )
        ids = await self._wait_for_approvals(po_name, seen=seen, kinds={"po_change", "send_email"})
        if not ids:
            return DemoStepOutcome(
                key="supplier_eta_reply",
                status="waiting",
                summary="the email is linked; the agent has not proposed the change yet, run "
                "the step again",
                links=self._links(po_name, []),
            )
        summary = f"the supplier confirmed {state.records['eta_new_date']}; the order change waits"
        if approve:
            ids = await self._approve_chain(po_name, ids, actor=actor)
            summary = f"the supplier confirmed {state.records['eta_new_date']}; the date moved"
        return DemoStepOutcome(
            key="supplier_eta_reply",
            status="done",
            summary=summary,
            links=self._links(po_name, ids),
            approval_ids=ids,
        )

    async def _step_risk_quote_round(
        self, state: DemoState, actor: DemoActor, approve: bool
    ) -> DemoStepOutcome:
        report = await self._risk.report()
        products = sorted(
            report.get("products", []),
            key=lambda p: float(p.get("p_stockout_30") or 0),
            reverse=True,
        )
        without_order = [
            p for p in products if not (p.get("late_po_names") or p.get("open_po_names"))
        ]
        candidates = products[:15]
        if not candidates:
            raise ScError("the risk radar lists no product; run the planner first")
        # A comparison needs rivals: among the riskiest, the product most suppliers list;
        # then one with nothing on order yet, then the higher risk.
        listed = await self._world.supplier_counts([int(p["product_id"]) for p in candidates])
        orderless = {int(p["product_id"]) for p in without_order}
        product = max(
            candidates,
            key=lambda p: (
                listed.get(int(p["product_id"]), 0),
                int(p["product_id"]) in orderless,
                float(p.get("p_stockout_30") or 0),
            ),
        )
        qty = float(product.get("suggested_qty") or 0) or 10.0
        label = str(product.get("product_name") or product.get("product") or product["product_id"])
        if product.get("product_ref"):
            label = f"[{product['product_ref']}] {label}"
        task = SourcingTask(
            kind="quote_round",
            case_id=f"demo_round_{new_id('r')}",
            product_id=int(product["product_id"]),
            qty=qty,
            reason=f"stockout risk {round(100 * float(product.get('p_stockout_30') or 0))}% "
            "at 30 days, from the demo",
        )
        result = await self._sourcing.run(task, requested_by=actor.email)
        if result["status"] in ("failed", "escalated"):
            raise ScError(str(result["summary"]))
        # The RFQs of the round, and the demo supplier's among them.
        rounds = await self._rounds.rounds(status=None)
        mine = next((r for r in rounds if r.get("case_id") == result["case_id"]), None)
        if mine is None and rounds:
            mine = max(rounds, key=lambda r: int(r.get("id") or 0))
        rfqs = list((mine or {}).get("rfqs") or [])
        rfq_names = [str(r["po_name"]) for r in rfqs if r.get("po_name")]
        late = state.records.get("late_order") or {}
        supplier_rfq = next(
            (
                str(r["po_name"])
                for r in rfqs
                if r.get("po_name")
                and int(r.get("partner_id") or 0) == int(late.get("partner_id") or -1)
            ),
            rfq_names[0] if rfq_names else None,
        )
        state.records.update(
            {
                "round_id": (mine or {}).get("id"),
                "round_case_id": result["case_id"],
                "rfq_names": rfq_names,
                "supplier_rfq": supplier_rfq,
                "risk_product": {
                    "product_id": product["product_id"],
                    "product": label,
                    "p_stockout_30": product.get("p_stockout_30"),
                    "qty": qty,
                },
            }
        )
        await self._store.save(state)
        ids: list[int] = []
        for name in rfq_names:
            ids.extend(await self._pending_ids(name) - set(state.approval_ids))
        if approve and ids:
            approved: list[int] = []
            for name in rfq_names:
                mine_ids = [i for i in ids if i in await self._pending_ids(name)]
                approved.extend(await self._approve_chain(name, mine_ids, actor=actor))
            ids = approved
        links = [DemoLink(label="risk radar", path="/risk")] + [
            DemoLink(label=name, path=f"/board?po={name}") for name in rfq_names
        ]
        for approval_id in ids:
            links.append(
                DemoLink(label=f"approval #{approval_id}", path=f"/approvals?id={approval_id}")
            )
        return DemoStepOutcome(
            key="risk_quote_round",
            status="done",
            summary=f"{label}: {result['summary']}",
            links=links,
            approval_ids=ids,
        )

    async def _step_supplier_quote(
        self, state: DemoState, actor: DemoActor, approve: bool
    ) -> DemoStepOutcome:
        rfq = state.records.get("supplier_rfq")
        if not rfq:
            raise ScError("no RFQ for the demo supplier in this run; run the quote round first")
        po_name = str(rfq)
        seen = self._seen(state)
        if not state.records.get("quote_sent"):
            mailbox = self._need_mailbox()
            if not await self._wait_outbound(po_name, more_than=0):
                return DemoStepOutcome(
                    key="supplier_quote",
                    status="waiting",
                    summary=f"the request {po_name} has not left yet: approve its email, then "
                    "run the step again",
                    links=self._links(po_name, []),
                )
            lines = await self._world.order_lines(po_name)
            if not lines:
                raise ScError(f"{po_name} has no lines to quote")
            # What the list said before the quote: the buyer's target for the counter-offer
            # (once recorded, the quote itself becomes the supplier's list entry).
            state.records["quote_list_price"] = min(line.price_unit for line in lines)
            quotes: dict[str, Any] = {
                po_name: {
                    "supplier": "Proveedor Hidraulica",
                    "email": self._cfg.supplier_email,
                    "lead_days": 20,
                    "note": "",
                    "lines": [
                        {
                            "product": line.product,
                            "qty": line.qty,
                            "price": round(line.price_unit * 1.12, 2),
                        }
                        for line in lines
                    ],
                }
            }
            # The rivals answer their own requests, each in character: one fast and dearer,
            # one cheap and slow. Only requests that actually left are answered.
            rivals = [n for n in state.records.get("rfq_names") or [] if n != po_name]
            for rival_po in rivals:
                if await self._world.outbound_count(rival_po) == 0:
                    continue
                name, email = await self._world.supplier_contact(rival_po)
                factor, lead_days, note = rival_terms(name, self._cfg.language)
                quotes[rival_po] = {
                    "supplier": name,
                    "email": email or self._cfg.supplier_email,
                    "lead_days": lead_days,
                    "note": note,
                    "lines": [
                        {
                            "product": line.product,
                            "qty": line.qty,
                            "price": round(line.price_unit * factor, 2),
                        }
                        for line in await self._world.order_lines(rival_po)
                    ],
                }
            for quoted_po, quote in quotes.items():
                if not quote["lines"]:
                    continue
                await self._mark_inbound(state, quoted_po)
                await mailbox.reply(
                    to=self._cfg.bot_email,
                    subject=subject_for("quote", quoted_po, self._cfg.language),
                    text=quote_text(
                        quoted_po,
                        quote["lines"],
                        lead_days=quote["lead_days"],
                        supplier=quote["supplier"],
                        note=quote["note"],
                        language=self._cfg.language,
                    ),
                    sender_name=quote["supplier"],
                    sender_email=quote["email"],
                )
            state.records["quotes"] = quotes
            state.records["quote_sent"] = True
            await self._store.save(state)
        if not await self._sync_until_linked(po_name, state):
            return DemoStepOutcome(
                key="supplier_quote",
                status="waiting",
                summary="the quote has not reached the mailbox yet; run the step again",
            )
        # The supplier agent records the quoted prices; a price change on an RFQ may itself
        # wait for a person. Let that settle, then ask for the counter-offer.
        quote_ids = await self._wait_for_approvals(po_name, seen=seen, kinds={"po_change"})
        if quote_ids and approve:
            quote_ids = await self._approve_chain(po_name, quote_ids, actor=actor)
        # the rivals' quotes are recorded the same way, each on its own request
        for rival_po in [n for n in (state.records.get("quotes") or {}) if n != po_name]:
            await self._sync_until_linked(rival_po, state)
            rival_ids = await self._wait_for_approvals(rival_po, seen=seen, kinds={"po_change"})
            if rival_ids and approve:
                rival_ids = await self._approve_chain(rival_po, rival_ids, actor=actor)
            quote_ids = quote_ids + rival_ids
        state.records["out_before_offer"] = await self._world.outbound_count(po_name)
        list_price = state.records.get("quote_list_price")
        task = SourcingTask(
            kind="counter_offer",
            case_id=f"demo_offer_{new_id('o')}",
            po_name=po_name,
            target_price=float(list_price) if list_price else None,
            reason="the quote is above the supplier's own list price (demo)",
        )
        result = await self._sourcing.run(task, requested_by=actor.email)
        if result["status"] in ("failed", "escalated"):
            return DemoStepOutcome(
                key="supplier_quote",
                status="waiting",
                summary=f"quote recorded; the counter-offer did not go: {result['summary']}",
                links=self._links(po_name, quote_ids),
                approval_ids=quote_ids,
            )
        ids = list(quote_ids)
        if result.get("approval_id"):
            ids.append(int(result["approval_id"]))
        summary = str(result["summary"])
        if approve and result.get("approval_id"):
            ids = quote_ids + await self._approve_chain(
                po_name, [int(result["approval_id"])], actor=actor
            )
            summary = f"{summary}; approved, the counter-offer is on its way"
        state.records["counter_offer_case"] = result["case_id"]
        state.records["counter_offer_made"] = bool(result.get("approval_id"))
        await self._store.save(state)
        return DemoStepOutcome(
            key="supplier_quote",
            status="done",
            summary=summary,
            links=self._links(po_name, ids),
            approval_ids=ids,
        )

    async def _step_award(
        self, state: DemoState, actor: DemoActor, approve: bool
    ) -> DemoStepOutcome:
        rfq = state.records.get("supplier_rfq")
        round_id = state.records.get("round_id")
        if not rfq or round_id is None:
            raise ScError("no round in this run; run the quote round first")
        po_name = str(rfq)
        seen = self._seen(state)
        if not state.records.get("acceptance_sent"):
            mailbox = self._need_mailbox()
            if state.records.get("counter_offer_made") and not await self._wait_outbound(
                po_name, more_than=int(state.records.get("out_before_offer") or 0)
            ):
                return DemoStepOutcome(
                    key="award",
                    status="waiting",
                    summary="the counter-offer has not left yet: approve it, then run the step "
                    "again",
                    links=self._links(po_name, []),
                )
            offered = await self._offered_price(state)
            await self._mark_inbound(state, po_name)
            message_id = await mailbox.reply(
                to=self._cfg.bot_email,
                subject=subject_for("quote", po_name, self._cfg.language),
                text=acceptance_text(po_name, offered, self._cfg.language),
            )
            state.records["acceptance_sent"] = message_id
            state.records["accepted_price"] = offered
            await self._store.save(state)
        if not await self._sync_until_linked(po_name, state):
            return DemoStepOutcome(
                key="award",
                status="waiting",
                summary="the acceptance has not reached the mailbox yet; run the step again",
            )
        accept_ids = await self._wait_for_approvals(po_name, seen=seen, kinds={"po_change"})
        if accept_ids and approve:
            accept_ids = await self._approve_chain(po_name, accept_ids, actor=actor)
        task = SourcingTask(
            kind="compare_quotes",
            case_id=f"{state.records.get('round_case_id', 'demo')}_cmp_{new_id('c')}",
            round_id=int(round_id),
            reason="compared for the demo",
        )
        result = await self._sourcing.run(task, requested_by=actor.email)
        if result["status"] in ("failed", "escalated"):
            return DemoStepOutcome(
                key="award",
                status="waiting",
                summary=f"the comparison did not conclude: {result['summary']}",
                links=self._links(po_name, accept_ids, DemoLink(label="rounds", path="/suppliers")),
                approval_ids=accept_ids,
            )
        ids = list(accept_ids)
        summary = str(result["summary"])
        if result.get("approval_id"):
            award_id = int(result["approval_id"])
            ids.append(award_id)
            if approve:
                ids = accept_ids + await self._approve_chain(po_name, [award_id], actor=actor)
                summary = f"{summary}; awarded by {actor.name}, the order is confirmed"
        return DemoStepOutcome(
            key="award",
            status="done",
            summary=summary,
            links=self._links(po_name, ids),
            approval_ids=ids,
        )

    async def _offered_price(self, state: DemoState) -> float | None:
        """The counter-offer's price, from the approval the sourcing agent raised."""
        for approval_id in reversed(state.approval_ids):
            try:
                approval = await self._approvals.get(approval_id)
            except ScError:
                continue
            if approval.kind != "negotiation_offer" or not approval.payload_json:
                continue
            try:
                payload = json.loads(approval.payload_json)
            except ValueError:
                continue
            offer = payload.get("offer") or payload.get("counter_offer") or payload
            price = offer.get("offered_price") if isinstance(offer, dict) else None
            if price:
                return float(price)
        return None

    async def _step_short_receipt(
        self, state: DemoState, actor: DemoActor, approve: bool
    ) -> DemoStepOutcome:
        order = state.records.get("receipt_order")
        if not order:
            raise ScError("no receipt order in this run; reset first")
        po_name = str(order["po_name"])
        seen = self._seen(state)
        if not state.records.get("receipt"):
            receipt = await self._world.receive_short(int(order["po_id"]), fraction=0.8)
            state.records["receipt"] = receipt.model_dump(mode="json")
            await self._store.save(state)
        receipt_info = state.records["receipt"]
        ids = await self._wait_for_approvals(po_name, seen=seen)
        summary = (
            f"{receipt_info['picking_name']}: {receipt_info['received']:g} of "
            f"{receipt_info['expected']:g} units received"
        )
        if not ids:
            return DemoStepOutcome(
                key="short_receipt",
                status="waiting",
                summary=f"{summary}; the logistics agent has not reported yet, run the step again",
                links=self._links(po_name, []),
            )
        if approve:
            ids = await self._approve_chain(po_name, ids, actor=actor)
            summary = f"{summary}; the discrepancy report goes to the supplier"
        else:
            summary = f"{summary}; the discrepancy report waits for a person"
        return DemoStepOutcome(
            key="short_receipt",
            status="done",
            summary=summary,
            links=self._links(po_name, ids),
            approval_ids=ids,
        )

    async def _step_invoice_variance(
        self, state: DemoState, actor: DemoActor, approve: bool
    ) -> DemoStepOutcome:
        order = state.records.get("receipt_order")
        if not order:
            raise ScError("no receipt order in this run; reset first")
        po_name = str(order["po_name"])
        seen = self._seen(state)
        if not state.records.get("bill"):
            ref = f"DEMO-{local_today().strftime('%y%m%d')}-{new_id('b')[-4:].upper()}"
            bill = await self._world.type_bill(int(order["po_id"]), ref=ref, uplift_pct=3.0)
            state.records["bill"] = bill.model_dump(mode="json")
            await self._store.save(state)
        bill_info = state.records["bill"]
        ids = await self._wait_for_approvals(po_name, seen=seen)
        summary = f"bill {bill_info['ref']} typed with one line 3% above the order"
        if not ids:
            return DemoStepOutcome(
                key="invoice_variance",
                status="waiting",
                summary=f"{summary}; the matching agent has not answered yet, run the step again",
                links=self._links(po_name, []),
            )
        if approve:
            ids = await self._approve_chain(po_name, ids, actor=actor)
            summary = f"{summary}; the variance was accepted by {actor.name}"
        else:
            summary = f"{summary}; the match waits for a person"
        return DemoStepOutcome(
            key="invoice_variance",
            status="done",
            summary=summary,
            links=self._links(po_name, ids),
            approval_ids=ids,
        )

    async def _step_briefing(
        self, state: DemoState, actor: DemoActor, approve: bool
    ) -> DemoStepOutcome:
        briefing = await self._briefing.build_and_save()
        counts = ", ".join(f"{k} {v}" for k, v in briefing.counts.items()) or "no sections"
        return DemoStepOutcome(
            key="briefing",
            status="done",
            summary=f"briefing for {briefing.day.isoformat()}: {counts}",
            links=[DemoLink(label="briefing", path="/briefing"), DemoLink(label="home", path="/")],
        )
