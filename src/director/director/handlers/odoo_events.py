"""Odoo business events.

* PO confirmed → ``supplier_comms.send_po``: the order PDF goes to the
  supplier with a request to confirm the delivery date. (The blueprint said
  ``request_eta``; phase 5.5 added ``send_po``, which sends the order *and*
  asks for the date, so it is the right first message on a fresh order.)
* Receipt validated → recorded on the case; the logistics agent (phase 9)
  will reconcile it.
* Orderpoint triggered → ``inventory_planning.review_product`` for that product.
* Approval resolved → mirrored on the case so the story shows who decided.
"""

from __future__ import annotations

from director.router import Dispatch, Route
from director.store import CaseKind
from sc_core.schema.a2a import InventoryPlanningTask, SupplierCommsTask
from sc_core.schema.events import (
    BaseEvent,
    OdooApprovalResolved,
    OdooOrderpointTriggered,
    OdooPurchaseConfirmed,
    OdooReceiptValidated,
)


def on_po_confirmed(event: BaseEvent) -> Route:
    assert isinstance(event, OdooPurchaseConfirmed)
    task = SupplierCommsTask(kind="send_po", case_id=event.case_id, po_name=event.po_name)
    return Route(
        case_kind="eta",
        po_name=event.po_name,
        partner_id=event.partner_id,
        dispatches=[Dispatch(agent="supplier_comms", task=task)],
        snapshot={
            "po_id": event.po_id,
            "po_name": event.po_name,
            "partner_id": event.partner_id,
            "date_planned": event.date_planned,
            "amount_total": event.amount_total,
            "currency": event.currency,
            "line_count": event.line_count,
            "confirmed_at": event.occurred_at,
        },
    )


def on_receipt(event: BaseEvent) -> Route:
    assert isinstance(event, OdooReceiptValidated)
    return Route(
        case_kind="receipt",
        po_name=event.po_name,
        partner_id=event.partner_id,
        note=f"receipt {event.picking_name} validated; logistics reconciliation arrives in phase 9",
    )


def on_orderpoint(event: BaseEvent) -> Route:
    assert isinstance(event, OdooOrderpointTriggered)
    task = InventoryPlanningTask(
        kind="review_product",
        case_id=event.case_id,
        product_ids=[event.product_id],
        context=(
            f"reorder rule {event.orderpoint_id} triggered: {event.qty_to_order:g} units of "
            f"{event.product_code or event.product_id} short"
        ),
    )
    return Route(case_kind="planning", dispatches=[Dispatch(agent="inventory_planning", task=task)])


def on_approval_resolved(event: BaseEvent) -> Route:
    assert isinstance(event, OdooApprovalResolved)
    return Route(
        case_kind=_case_kind_for_approval(event.kind),
        po_name=event.po_name,
        note=approval_note(event),
    )


_APPROVAL_LABELS: dict[str, str] = {
    "send_email": "email",
    "po_change": "order change",
    "planning_run": "planning run",
    "escalation": "escalation",
    "unlinked_mail": "unlinked email",
    "orderpoint_change": "reorder rule change",
}


def approval_note(event: OdooApprovalResolved) -> str:
    """``Approval #20 (planning run) expired: nobody answered in time``."""
    label = _APPROVAL_LABELS.get(event.kind, event.kind.replace("_", " "))
    person = event.resolved_by_name or event.resolved_by
    if event.status == "expired":
        outcome = "expired: nobody answered in time"
    else:
        outcome = f"{event.status} by {person}" if person else event.status
    return f"Approval #{event.approval_id} ({label}) {outcome}"


_APPROVAL_CASE_KINDS: dict[str, CaseKind] = {
    "send_email": "rfq",
    "po_change": "inbound",
    "unlinked_mail": "unlinked",
    "orderpoint_change": "planning",
    "planning_run": "planning",
}


def _case_kind_for_approval(kind: str) -> CaseKind:
    return _APPROVAL_CASE_KINDS.get(kind, "inbound")
