"""Odoo business events.

* PO confirmed → ``supplier_comms.send_po``: the order PDF goes to the
  supplier with a request to confirm the delivery date. (The blueprint said
  ``request_eta``; phase 5.5 added ``send_po``, which sends the order *and*
  asks for the date, so it is the right first message on a fresh order.)
* Receipt validated → recorded on the case; the logistics agent (phase 9)
  will reconcile it.
* Orderpoint triggered → recorded; the planning agent (phase 7) reviews it.
* Approval resolved → mirrored on the case so the story shows who decided.
"""

from __future__ import annotations

from director.router import Dispatch, Route
from director.store import CaseKind
from sc_core.schema.a2a import SupplierCommsTask
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
    return Route(
        case_kind="planning",
        note=(
            f"reorder rule {event.orderpoint_id} needs {event.qty_to_order:g} of product "
            f"{event.product_code or event.product_id}; planning agent arrives in phase 7"
        ),
    )


def on_approval_resolved(event: BaseEvent) -> Route:
    assert isinstance(event, OdooApprovalResolved)
    who = f" by {event.resolved_by}" if event.resolved_by else ""
    return Route(
        case_kind=_case_kind_for_approval(event.kind),
        po_name=event.po_name,
        note=f"approval {event.approval_id} ({event.kind}) {event.status}{who}",
    )


_APPROVAL_CASE_KINDS: dict[str, CaseKind] = {
    "send_email": "rfq",
    "po_change": "inbound",
    "unlinked_mail": "unlinked",
    "orderpoint_change": "planning",
    "planning_run": "planning",
}


def _case_kind_for_approval(kind: str) -> CaseKind:
    return _APPROVAL_CASE_KINDS.get(kind, "inbound")
