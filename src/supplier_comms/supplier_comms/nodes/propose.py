"""Turn extracted data into a change proposal against what Odoo has. No model call.

- A delivery date that differs from a line's planned date proposes
  ``date_planned`` on that line (all lines when the supplier gave one date).
- A quoted unit price that differs from the line price proposes ``price``
  (recorded on the supplier price list, never on the confirmed order line).
- A lead time proposes ``lead_days`` on the price list.

``needs_review`` is set when the currency differs from the order's, when a
quoted line could not be mapped, or when confidence is below 0.7. Reviewed
changes are shown to the human but never written by ``apply_changes``.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from sc_core.schema.a2a import ChangeProposal, ProposedChange, QuotationData
from supplier_comms.models import LineView, PoContext
from supplier_comms.nodes.common import context_of, finish
from supplier_comms.state import Node

REVIEW_THRESHOLD = 0.7


def build_proposal(ctx: PoContext, data: QuotationData) -> ChangeProposal:
    changes: list[ProposedChange] = []
    low = data.confidence < REVIEW_THRESHOLD

    if data.eta_date is not None:
        for line in ctx.lines:
            if line.date_planned != data.eta_date:
                changes.append(
                    ProposedChange(
                        po_line_id=line.id,
                        product=line.product,
                        field="date_planned",
                        before=line.date_planned.isoformat() if line.date_planned else None,
                        after=data.eta_date.isoformat(),
                        confidence=data.confidence,
                        needs_review=low,
                        review_reason="fecha interpretada con baja confianza" if low else None,
                    )
                )

    lines_by_id = {line.id: line for line in ctx.lines}
    for quoted in data.lines:
        matched = lines_by_id.get(quoted.po_line_id) if quoted.po_line_id is not None else None
        if matched is None:
            if quoted.unit_price is not None:
                changes.append(
                    ProposedChange(
                        po_line_id=0,
                        product=quoted.description,
                        field="price",
                        before=None,
                        after=_money(quoted.unit_price, quoted.currency or data.currency),
                        confidence=quoted.confidence,
                        needs_review=True,
                        review_reason="producto no emparejado con una línea de la orden",
                    )
                )
            continue
        line = matched
        currency = quoted.currency or data.currency
        mismatch = bool(currency) and bool(line.currency) and currency != line.currency
        line_low = quoted.confidence < REVIEW_THRESHOLD or low
        if quoted.unit_price is not None and abs(quoted.unit_price - line.price_unit) > 1e-6:
            changes.append(
                ProposedChange(
                    po_line_id=line.id,
                    product=line.product,
                    field="price",
                    before=_money(line.price_unit, line.currency),
                    after=_money(quoted.unit_price, currency or line.currency),
                    confidence=min(quoted.confidence, data.confidence),
                    needs_review=mismatch or line_low,
                    review_reason=_reason(mismatch, line_low, line),
                )
            )
        if quoted.lead_days is not None:
            changes.append(
                ProposedChange(
                    po_line_id=line.id,
                    product=line.product,
                    field="lead_days",
                    before=None,
                    after=str(quoted.lead_days),
                    confidence=min(quoted.confidence, data.confidence),
                    needs_review=line_low,
                    review_reason="baja confianza" if line_low else None,
                )
            )

    summary = _summary(changes, data)
    return ChangeProposal(changes=changes, summary=summary)


def make_propose_changes() -> Node:
    async def propose_changes(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        data = QuotationData.model_validate(state["extracted"])
        proposal = build_proposal(ctx, data)
        if not proposal.changes:
            logger.bind(po_name=ctx.name).info("nothing to change")
            return finish(
                "no_action",
                f"la respuesta del proveedor no cambia nada en {ctx.name}",
                proposal=proposal.model_dump(mode="json"),
            )
        logger.bind(
            po_name=ctx.name, changes=len(proposal.changes), review=proposal.needs_review
        ).info("changes proposed")
        return {"proposal": proposal.model_dump(mode="json")}

    return propose_changes


def _money(amount: float, currency: str | None) -> str:
    return f"{amount:.2f} {currency}".strip()


def _reason(mismatch: bool, low: bool, line: LineView) -> str | None:
    if mismatch:
        return f"moneda distinta a la de la orden ({line.currency})"
    if low:
        return "baja confianza"
    return None


def _summary(changes: list[ProposedChange], data: QuotationData) -> str:
    if not changes:
        return "sin cambios respecto a Odoo"
    dates = [c for c in changes if c.field == "date_planned"]
    prices = [c for c in changes if c.field == "price"]
    leads = [c for c in changes if c.field == "lead_days"]
    parts = []
    if dates:
        parts.append(f"fecha de entrega {dates[0].after} en {len(dates)} línea(s)")
    if prices:
        parts.append(f"{len(prices)} precio(s)")
    if leads:
        parts.append(f"{len(leads)} plazo(s)")
    review = sum(1 for c in changes if c.needs_review)
    text = ", ".join(parts)
    if review:
        text += f"; {review} para revisar"
    if data.eta_date_raw and dates:
        text += f' (proveedor: "{data.eta_date_raw}")'
    return text[:500]
