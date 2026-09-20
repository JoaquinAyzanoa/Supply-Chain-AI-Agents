"""The board: one card per purchase order, in the column its life is at.

Columns follow Odoo's order states and the agents' work on them:

    proposed        draft RFQs (the planner's proposals, or drafts made in Odoo)
    rfq_sent        RFQ sent, waiting for the supplier's quotation
    quote_received  the supplier answered and a person must accept the change
    confirmed       confirmed order, delivery still far away
    incoming        delivery due within ``due_soon_days`` or already late
    received        receipt validated in the warehouse
    closed          done or cancelled

Cards carry what a buyer looks for first: delivery colour (on time, due
soon, late), days without an answer, the next automatic step and its date,
a pending approval, an escalation, a hold. Dragging a card is a real action
when Odoo allows it (confirm an RFQ, send a proposal, close an order); the
other transitions happen through events and the API says so.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.approvals import ApprovalsGateway
from director.api.auth import Approver, Principal, Viewer
from director.api.exceptions import ExceptionsSource
from director.api.performance import PerformanceSource
from director.api.planning import PlanningReadStore
from director.handlers.followups import MailActivity
from director.playbooks import PlaybookEngine, PlaybookPosition
from director.policies import next_action
from director.store import Case, CaseStore
from director.workflow import Deps, consolidate_outcome, outcome_from_reply
from sc_core.infra.settings import Settings
from sc_core.odoo.links import record_url
from sc_core.odoo.models import Approval, ApprovalKind, PurchaseOrder, PurchaseOrderLine
from sc_core.odoo.repositories import PurchaseOrderRepo
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.idempotency import new_id
from sc_core.shared.time import local_today

router = APIRouter(prefix="/board", tags=["board"])

Column = Literal[
    "proposed",
    "rfq_sent",
    "quote_received",
    "confirmed",
    "incoming",
    "received",
    "invoicing",
    "closed",
]
COLUMNS: tuple[Column, ...] = (
    "proposed",
    "rfq_sent",
    "quote_received",
    "confirmed",
    "incoming",
    "received",
    "invoicing",
    "closed",
)
Delivery = Literal["on_time", "due_soon", "late", "none"]
ActKind = Literal["late_po", "rfq_no_reply"]


class PendingApproval(StrictModel):
    id: int
    kind: str
    summary: str
    requested_by: str | None = None


class PlaybookPositions(Protocol):
    async def position_for_po(self, po_name: str) -> PlaybookPosition | None: ...


class BoardCard(StrictModel):
    po_id: int
    po_name: str
    partner_id: int
    partner_name: str
    buyer: str | None = None
    amount_total: float = 0.0
    currency: str | None = None
    state: str
    receipt_status: str | None = None
    date_planned: date | None = None
    eta_source: str | None = None
    supplier_confirmed: bool = False
    invoice_status: str | None = None
    discrepancy: bool = False
    column: Column
    delivery: Delivery = "none"
    days_late: int = 0
    days_silent: int | None = None
    last_outbound: date | None = None
    last_inbound: date | None = None
    next_action: str | None = None
    next_action_at: date | None = None
    pending_approval: PendingApproval | None = None
    escalated: bool = False
    on_hold_until: date | None = None
    case_id: str | None = None
    case_code: str | None = None
    case_status: str | None = None
    summary: str | None = None
    act_kind: ActKind | None = None
    can_act: bool = False
    odoo_url: str
    playbook: PlaybookPosition | None = None  # where the order is in its playbook
    age_days: int = 0  # since the order was created
    predicted_delay_days: int | None = None  # S8: the supplier's usual drift, before it is late
    delay_confidence: float | None = None  # from the supplier's OTIF


class PlanningPending(StrictModel):
    """A daily plan waiting for review: its proposals become cards once approved."""

    approval_id: int
    run_id: str | None = None
    as_of: str | None = None
    summary: str


class Board(StrictModel):
    as_of: date
    due_soon_days: int
    cards: list[BoardCard]
    counts: dict[str, int]
    planning: PlanningPending | None = None


class MoveRequest(StrictModel):
    to: Column
    note: str | None = Field(default=None, max_length=500)


class MoveResponse(StrictModel):
    po_name: str
    column: Column
    message: str


class SupplierConfirmedRequest(StrictModel):
    value: bool


@runtime_checkable
class BoardOrders(Protocol):
    async def board_orders(self, *, closed_since: date) -> list[PurchaseOrder]: ...

    async def by_names(self, names: list[str]) -> list[PurchaseOrder]: ...

    async def lines(self, po_id: int) -> list[PurchaseOrderLine]: ...

    async def confirm(self, po_id: int) -> PurchaseOrder: ...

    async def cancel(self, po_id: int) -> None: ...

    async def mark_done(self, po_id: int) -> None: ...

    async def set_supplier_confirmed(self, po_id: int, value: bool) -> None: ...

    async def post_note(self, po_id: int, body_html: str) -> int: ...


class OdooBoardOrders:
    def __init__(self, orders: PurchaseOrderRepo) -> None:
        self._orders = orders

    async def board_orders(self, *, closed_since: date) -> list[PurchaseOrder]:
        return await self._orders.board_orders(closed_since=closed_since)

    async def by_names(self, names: list[str]) -> list[PurchaseOrder]:
        return await self._orders.by_names(names)

    async def lines(self, po_id: int) -> list[PurchaseOrderLine]:
        return await self._orders.lines(po_id)

    async def confirm(self, po_id: int) -> PurchaseOrder:
        return await self._orders.confirm(po_id)

    async def cancel(self, po_id: int) -> None:
        await self._orders.cancel(po_id)

    async def mark_done(self, po_id: int) -> None:
        await self._orders.mark_done(po_id)

    async def set_supplier_confirmed(self, po_id: int, value: bool) -> None:
        await self._orders.set_supplier_confirmed(po_id, value)

    async def post_note(self, po_id: int, body_html: str) -> int:
        return await self._orders.post_note(po_id, body_html)


# --- building the board -----------------------------------------------------------------------

DUE_SOON_DAYS = 5
CLOSED_DAYS = 30


def column_for(
    po: PurchaseOrder, *, pending_kinds: set[ApprovalKind], today: date, due_soon_days: int
) -> Column:
    if po.state == "draft":
        return "proposed"
    if po.state in ("sent", "to approve"):
        return "quote_received" if "po_change" in pending_kinds else "rfq_sent"
    if po.state == "purchase":
        if po.receipt_status == "full":
            # received and waiting for the supplier's invoice; then checked or drafted
            invoiced = "vendor_bill" in pending_kinds or po.invoice_status == "invoiced"
            return "invoicing" if invoiced else "received"
        if po.date_planned is not None and (po.date_planned.date() - today).days <= due_soon_days:
            return "incoming"
        return "confirmed"
    return "closed"


def delivery_for(po: PurchaseOrder, *, today: date, due_soon_days: int) -> tuple[Delivery, int]:
    if po.state != "purchase" or po.date_planned is None or po.receipt_status == "full":
        return "none", 0
    delta = (po.date_planned.date() - today).days
    if delta < 0:
        return "late", -delta
    if delta <= due_soon_days:
        return "due_soon", 0
    return "on_time", 0


def predicted_delay(
    po: PurchaseOrder, score: dict[str, Any] | None, delivery: Delivery
) -> tuple[int | None, float | None]:
    """How late the supplier usually delivers against its promise, for an order that is
    confirmed and not late yet; the confidence follows the supplier's OTIF."""
    if score is None or delivery not in ("on_time", "due_soon") or po.state != "purchase":
        return None, None
    drift = score.get("promise_drift_days")
    if drift is None or float(drift) < 0.5:
        return None, None
    otif = score.get("otif")
    confidence = round(min(0.95, 0.4 + (1 - float(otif)) * 0.6), 2) if otif is not None else 0.5
    return round(float(drift)), confidence


async def build_board(
    *,
    orders: BoardOrders,
    cases: CaseStore,
    approvals: ApprovalsGateway,
    mail: MailActivity,
    source: ExceptionsSource,
    settings: Settings,
    today: date,
    due_soon_days: int = DUE_SOON_DAYS,
    playbooks: PlaybookPositions | None = None,
    performance: PerformanceSource | None = None,
) -> Board:
    rows = await orders.board_orders(closed_since=today - timedelta(days=CLOSED_DAYS))
    scores: dict[int, dict[str, Any]] = {}
    if performance is not None:
        try:
            scores = {
                int(r["partner_id"]): r
                for r in await performance.scores()
                if r.get("partner_id") is not None
            }
        except ScError as exc:
            logger.warning("scores unavailable for the board: {}", exc)
    pending = await approvals.list(status="pending", kind=None, po_name=None)
    by_po: dict[str, list[Approval]] = {}
    for approval in pending:
        if approval.po_id is not None:
            by_po.setdefault(approval.po_id.name, []).append(approval)
    # An order someone still has to decide on belongs on the board however old it is
    # (an invoice for a receipt of months ago, for instance).
    known = {po.name for po in rows}
    missing = sorted({name for name in by_po if name not in known})
    if missing:
        rows = [*rows, *await orders.by_names(missing)]
    contacts = await mail.contacts()
    policy = await source.effective_policy()
    facts = {f.po_name: f for f in await source.gather(today)}
    cards: list[BoardCard] = []
    for po in rows:
        mine = by_po.get(po.name, [])
        kinds = {a.kind for a in mine}
        column = column_for(po, pending_kinds=kinds, today=today, due_soon_days=due_soon_days)
        delivery, days_late = delivery_for(po, today=today, due_soon_days=due_soon_days)
        last_out, last_in = contacts.get(po.name, (None, None))
        open_cases = await cases.open_for_po(po.name)
        # No open case: the panel still shows the last one, so a closed story stays readable.
        latest = open_cases or await cases.list(po_name=po.name, limit=1)
        case: Case | None = latest[0] if latest else None
        fact = facts.get(po.name)
        step = next_action(fact, policy, today) if fact else None
        first = next((a for a in mine if a.kind != "escalation"), None) or (
            mine[0] if mine else None
        )
        act_kind: ActKind | None = None
        if fact is not None:
            if fact.is_confirmed_open and fact.date_planned and today > fact.date_planned:
                act_kind = "late_po"
            elif fact.is_rfq and (fact.silent_days(today) or 0) > 0:
                act_kind = "rfq_no_reply"
        predicted, confidence = predicted_delay(po, scores.get(po.partner_id.id), delivery)
        cards.append(
            BoardCard(
                po_id=po.id,
                po_name=po.name,
                partner_id=po.partner_id.id,
                partner_name=po.partner_id.name,
                buyer=po.user_id.name if po.user_id else None,
                amount_total=po.amount_total,
                currency=po.currency_id.name if po.currency_id else None,
                state=po.state,
                receipt_status=po.receipt_status,
                date_planned=po.date_planned.date() if po.date_planned else None,
                eta_source=po.sc_eta_source,
                supplier_confirmed=po.sc_supplier_confirmed or po.sc_eta_source == "supplier",
                column=column,
                delivery=delivery,
                days_late=days_late,
                days_silent=fact.silent_days(today) if fact else None,
                last_outbound=last_out,
                last_inbound=last_in,
                next_action=(
                    "escalate"  # the page's words: task.escalate
                    if step and step.escalate
                    else (step.task if step else None)
                ),
                next_action_at=step.due if step else None,
                pending_approval=(
                    PendingApproval(
                        id=first.id,
                        kind=first.kind,
                        summary=first.summary,
                        requested_by=first.requested_by,
                    )
                    if first
                    else None
                ),
                invoice_status=po.invoice_status,
                discrepancy=any(
                    a.kind == "send_email" and a.requested_by == "logistics" for a in mine
                ),
                escalated=any(a.kind == "escalation" for a in mine)
                or (bool(open_cases) and open_cases[0].status == "escalated"),
                on_hold_until=(
                    case.next_action_at.date()
                    if case and case.next_action_at and case.next_action_at.date() > today
                    else None
                ),
                playbook=await playbooks.position_for_po(po.name) if playbooks else None,
                case_id=case.case_id if case else None,
                case_code=case.code if case else None,
                case_status=case.status if case else None,
                summary=case.summary if case else None,
                act_kind=act_kind,
                can_act=act_kind is not None and fact is not None and not fact.awaiting_human,
                odoo_url=record_url(settings.odoo.browser_url, "purchase.order", po.id),
                age_days=max(0, (today - po.date_order.date()).days) if po.date_order else 0,
                predicted_delay_days=predicted,
                delay_confidence=confidence,
            )
        )
    order = {name: i for i, name in enumerate(COLUMNS)}
    cards.sort(key=lambda c: (order[c.column], -c.days_late, c.date_planned or date.max, c.po_name))
    counts: dict[str, int] = {name: sum(1 for c in cards if c.column == name) for name in COLUMNS}
    plan = next((a for a in pending if a.kind == "planning_run"), None)
    planning = None
    if plan is not None:
        payload = json.loads(plan.payload_json) if plan.payload_json else {}
        planning = PlanningPending(
            approval_id=plan.id,
            run_id=plan.run_id or (str(payload["run_id"]) if payload.get("run_id") else None),
            as_of=str(payload["as_of"]) if payload.get("as_of") else None,
            summary=plan.summary,
        )
    return Board(
        as_of=today, due_soon_days=due_soon_days, cards=cards, counts=counts, planning=planning
    )


# --- moves ------------------------------------------------------------------------------------


class BoardMoves:
    """What a drag does in Odoo, when Odoo allows it."""

    def __init__(self, orders: BoardOrders, deps: Deps) -> None:
        self._orders = orders
        self._deps = deps

    async def move(self, po: PurchaseOrder, to: Column, *, by: Principal, note: str | None) -> str:
        current = column_for(
            po, pending_kinds=set(), today=local_today(), due_soon_days=DUE_SOON_DAYS
        )
        if to == current:
            return "already there"
        if to == "confirmed" and po.state in ("draft", "sent", "to approve"):
            await self._orders.confirm(po.id)
            await self._note(po, by, f"Order confirmed from the Control Tower by {by.name}", note)
            return f"{po.name} confirmed; the purchase order goes to the supplier for approval"
        if to == "rfq_sent" and po.state == "draft":
            return await self._send_rfq(po, by)
        if to == "closed":
            if po.state in ("draft", "sent", "to approve"):
                await self._orders.cancel(po.id)
                await self._note(po, by, f"Cancelled from the Control Tower by {by.name}", note)
                return f"{po.name} cancelled"
            if po.state == "purchase" and po.receipt_status == "full":
                await self._orders.mark_done(po.id)
                await self._note(
                    po, by, f"Locked as done from the Control Tower by {by.name}", note
                )
                return f"{po.name} closed"
            raise HTTPException(
                status_code=422,
                detail="a confirmed order with goods still to receive is closed in Odoo: "
                "receive it in the warehouse or cancel it there",
            )
        raise HTTPException(
            status_code=422,
            detail=f"an order does not move to '{to}' by hand: that column follows Odoo and "
            "the supplier's answers",
        )

    async def set_supplier_confirmed(self, po: PurchaseOrder, value: bool, *, by: Principal) -> str:
        await self._orders.set_supplier_confirmed(po.id, value)
        text = (
            f"Supplier confirmed the order (marked by {by.name})"
            if value
            else f"Supplier confirmation unmarked by {by.name}"
        )
        await self._note(po, by, text, None)
        return text

    async def _send_rfq(self, po: PurchaseOrder, by: Principal) -> str:
        case, _ = await self._deps.cases.attach_or_create(
            kind="rfq", po_name=po.name, partner_id=po.partner_id.id, agent="supplier_comms"
        )
        thread_id = f"board_{new_id('t')}"
        task = SupplierCommsTask(kind="send_rfq", case_id=thread_id, po_name=po.name)
        await self._deps.cases.add_event(
            case.case_id,
            "task_sent",
            {
                "agent": "supplier_comms",
                "task": task.kind,
                "thread_id": thread_id,
                "po_name": po.name,
                "by": by.email,
            },
        )
        try:
            reply = await self._deps.agents.for_name("supplier_comms").send(
                task.model_dump_json(), case_id=case.case_id
            )
        except ScError as exc:
            outcome = outcome_from_reply(
                case, "supplier_comms", task.kind, thread_id, None, error=exc
            )
        else:
            outcome = outcome_from_reply(case, "supplier_comms", task.kind, thread_id, reply)
        await consolidate_outcome(
            self._deps.cases, self._deps.escalator, self._deps.conversations, outcome
        )
        return outcome.summary

    async def _note(self, po: PurchaseOrder, by: Principal, text: str, note: str | None) -> None:
        body = f"<p>{text}.{' ' + note if note else ''}</p>"
        try:
            await self._orders.post_note(po.id, body)
        except ScError as exc:  # the move happened; a missing note is not worth failing
            logger.warning("board note not posted on {}: {}", po.name, exc)
        for case in await self._deps.cases.open_for_po(po.name):
            await self._deps.cases.add_event(
                case.case_id,
                "note",
                {"text": f"{text}{' — ' + note if note else ''}", "by": by.email},
            )


# --- routes -----------------------------------------------------------------------------------


@router.get("", response_model=Board)
async def board(
    due_soon_days: int = Query(default=DUE_SOON_DAYS, ge=0, le=60),
    _: Principal = Viewer,
    orders: BoardOrders = Injected(BoardOrders),  # type: ignore[type-abstract]
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    approvals: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
    mail: MailActivity = Injected(MailActivity),  # type: ignore[type-abstract]
    source: ExceptionsSource = Injected(ExceptionsSource),  # type: ignore[type-abstract]
    settings: Settings = Injected(Settings),
    playbooks: PlaybookEngine = Injected(PlaybookEngine),
    performance: PerformanceSource = Injected(PerformanceSource),  # type: ignore[type-abstract]
) -> Board:
    return await build_board(
        orders=orders,
        cases=cases,
        approvals=approvals,
        mail=mail,
        source=source,
        settings=settings,
        today=local_today(),
        due_soon_days=due_soon_days,
        playbooks=playbooks,
        performance=performance,
    )


async def _order(orders: BoardOrders, po_name: str) -> PurchaseOrder:
    rows = await orders.board_orders(closed_since=local_today() - timedelta(days=CLOSED_DAYS))
    po = next((r for r in rows if r.name == po_name), None)
    if po is None:
        raise HTTPException(status_code=404, detail=f"{po_name} is not on the board")
    return po


class OrderLineView(StrictModel):
    line_id: int
    product: str
    qty: float
    qty_received: float
    qty_invoiced: float
    price_unit: float
    subtotal: float
    date_planned: datetime | None = None


class OrderOrigin(StrictModel):
    """Where the order came from: the planner (with its reasoning) or a person in Odoo."""

    kind: Literal["planning", "odoo"]
    run_id: str | None = None
    as_of: date | None = None
    summary: str | None = None
    explanations: list[str] = []
    created_by: str | None = None
    origin: str | None = None


class OrderDetail(StrictModel):
    po_name: str
    lines: list[OrderLineView]
    origin: OrderOrigin


def parse_plan_ref(external_ref: str | None) -> str | None:
    """``plan:<run_id>:<supplier>:<warehouse>`` is how the planner tags the RFQs it creates."""
    if not external_ref or not external_ref.startswith("plan:"):
        return None
    parts = external_ref.split(":")
    return parts[1] if len(parts) >= 2 and parts[1] else None


async def order_origin(
    po: PurchaseOrder, lines: list[PurchaseOrderLine], planning: PlanningReadStore
) -> OrderOrigin:
    run_id = parse_plan_ref(po.sc_external_ref)
    if run_id is not None:
        run = await planning.run(run_id)
        product_ids = {ln.product_id.id for ln in lines if ln.product_id is not None}
        rows = await planning.lines(run_id) if run else []
        explanations = [
            f"{row.line.product_ref}: {row.line.explanation}"
            for row in rows
            if row.line.product_id in product_ids and row.line.explanation
        ]
        return OrderOrigin(
            kind="planning",
            run_id=run_id,
            as_of=run.as_of if run else None,
            summary=run.summary if run else None,
            explanations=explanations,
        )
    return OrderOrigin(
        kind="odoo",
        created_by=po.user_id.name if po.user_id else None,
        origin=po.origin or None,
    )


@router.get("/{po_name}/detail", response_model=OrderDetail)
async def order_detail(
    po_name: str,
    _: Principal = Viewer,
    orders: BoardOrders = Injected(BoardOrders),  # type: ignore[type-abstract]
    planning: PlanningReadStore = Injected(PlanningReadStore),  # type: ignore[type-abstract]
) -> OrderDetail:
    """The order's lines and where it came from, for the drawer."""
    po = await _order(orders, po_name)
    lines = await orders.lines(po.id)
    return OrderDetail(
        po_name=po.name,
        lines=[
            OrderLineView(
                line_id=ln.id,
                product=ln.product_id.name if ln.product_id else ln.name,
                qty=ln.product_qty,
                qty_received=ln.qty_received,
                qty_invoiced=ln.qty_invoiced,
                price_unit=ln.price_unit,
                subtotal=ln.price_subtotal,
                date_planned=ln.date_planned,
            )
            for ln in lines
        ],
        origin=await order_origin(po, lines, planning),
    )


@router.post("/{po_name}/move", response_model=MoveResponse)
async def move_card(
    po_name: str,
    body: MoveRequest,
    principal: Principal = Approver,
    orders: BoardOrders = Injected(BoardOrders),  # type: ignore[type-abstract]
    moves: BoardMoves = Injected(BoardMoves),
) -> MoveResponse:
    po = await _order(orders, po_name)
    try:
        message = await moves.move(po, body.to, by=principal, note=body.note)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=f"Odoo refused: {exc.message}") from exc
    logger.bind(po_name=po_name, to=body.to, by=principal.email).info("board move")
    return MoveResponse(po_name=po_name, column=body.to, message=message)


@router.post("/{po_name}/supplier-confirmed", response_model=MoveResponse)
async def supplier_confirmed(
    po_name: str,
    body: SupplierConfirmedRequest,
    principal: Principal = Approver,
    orders: BoardOrders = Injected(BoardOrders),  # type: ignore[type-abstract]
    moves: BoardMoves = Injected(BoardMoves),
) -> MoveResponse:
    po = await _order(orders, po_name)
    try:
        message = await moves.set_supplier_confirmed(po, body.value, by=principal)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=f"Odoo refused: {exc.message}") from exc
    column = column_for(po, pending_kinds=set(), today=local_today(), due_soon_days=DUE_SOON_DAYS)
    return MoveResponse(po_name=po_name, column=column, message=message)
