"""Receipt reconciliation: counted against expected, per line, no model call.

A line is ``short`` when the clerk counted less than the receipt expected
and ``over`` when more, beyond the tolerance (a percentage of the expected
quantity; 0 means exact). Move lines that belong to no order line are
``over`` against an expectation of zero. Damage is reported by people, not
detected here: a ``report_discrepancy`` task carries their words into the
email.
"""

from __future__ import annotations

from logistics.models import ReceiptView
from sc_core.schema.a2a import DiscrepancyKind, ReceiptDiscrepancy, ReceiptReconciliation

EPSILON = 1e-6


def reconcile(receipt: ReceiptView, *, tolerance_pct: float = 0.0) -> ReceiptReconciliation:
    if tolerance_pct < 0:
        raise ValueError("tolerance_pct must be zero or positive")
    found: list[ReceiptDiscrepancy] = []
    kind: DiscrepancyKind
    for line in receipt.lines:
        allowed = abs(line.expected) * tolerance_pct / 100.0
        diff = line.received - line.expected
        if diff < 0 and -diff > allowed + EPSILON:
            kind = "short"
        elif diff > 0 and diff > allowed + EPSILON:
            kind = "over"
        else:
            continue
        found.append(
            ReceiptDiscrepancy(
                po_line_id=line.po_line_id,
                product=line.product,
                kind=kind,
                expected=line.expected,
                received=line.received,
                uom=line.uom,
            )
        )
    return ReceiptReconciliation(
        picking_id=receipt.picking_id,
        picking_name=receipt.name,
        lines=len(receipt.lines),
        tolerance_pct=tolerance_pct,
        discrepancies=found,
    )
