"""Find the order and compare, in code.

The order comes from the task, from the invoice's reference, from the email
text, or from the supplier's open orders by total. An invoice number the
supplier already billed is recorded once and left alone.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from invoice_match.domain.matching import build_match, find_po_reference, pick_by_amount
from invoice_match.domain.models import BillView, PoCandidate
from invoice_match.graph.nodes.common import bill_of, finish, invoice_of, task_of
from invoice_match.graph.state import Node
from invoice_match.infra.ports import InvoicePorts
from sc_core.i18n import Language, t
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from supplier_comms.domain.models import PoContext


def without_own_bill(ctx: PoContext, bill: BillView) -> PoContext:
    """Odoo counts a draft bill's quantities as invoiced already; checking that very bill
    must not see its own lines as "billed before", so they are taken back out."""
    own: dict[int, float] = {}
    for line in bill.lines:
        if line.purchase_line_id is not None:
            own[line.purchase_line_id] = own.get(line.purchase_line_id, 0.0) + line.qty
    if not own:
        return ctx
    return ctx.model_copy(
        update={
            "lines": [
                line.model_copy(
                    update={"qty_invoiced": max(line.qty_invoiced - own.get(line.id, 0.0), 0.0)}
                )
                for line in ctx.lines
            ]
        }
    )


def make_match(
    ports: InvoicePorts,
    *,
    price_tolerance_pct: float,
    qty_tolerance_pct: float,
    fuzzy_threshold: float,
    language: Language = "en",
    runtime: RuntimeSettingsReader | None = None,
) -> Node:
    tolerance = price_tolerance_pct

    async def match(state: Any) -> dict[str, Any]:
        price_tolerance_pct = tolerance
        if runtime is not None:
            override = (await runtime.current()).invoice_price_tolerance_pct
            if override is not None:
                price_tolerance_pct = override
        task = task_of(state)
        invoice = invoice_of(state)
        bill = bill_of(state)
        update: dict[str, Any] = {}
        ctx = PoContext.model_validate(state["po_context"]) if state.get("po_context") else None
        supplier_id = state.get("supplier_id")

        # The same invoice number twice is the same invoice: nothing to create.
        if bill is None and invoice.invoice_number and supplier_id:
            existing = await ports.existing_bill(int(supplier_id), invoice.invoice_number)
            if existing is not None:
                _, name = existing
                return finish(
                    "no_action",
                    t("bill.already", language, number=invoice.invoice_number, bill=name or "-"),
                )

        if ctx is None:
            po_name = find_po_reference(invoice, state.get("inbound_text") or "")
            if po_name:
                ctx = await ports.load_po(po_name)
            if ctx is None and supplier_id:
                candidates = await ports.candidates(int(supplier_id))
                update["candidates"] = [c.model_dump(mode="json") for c in candidates]
                chosen, reasons = pick_by_amount(
                    invoice, candidates, tolerance_pct=max(price_tolerance_pct, 0.5)
                )
                if chosen is not None:
                    ctx = await ports.load_po(chosen.name)
                elif reasons:
                    return {
                        **update,
                        **finish("escalated", t("bill.no_order", language, why="; ".join(reasons))),
                    }
            if ctx is None:
                return {**update, **finish("escalated", t("bill.no_supplier", language))}
            update["po_context"] = ctx.model_dump(mode="json")

        if bill is not None:
            ctx = without_own_bill(ctx, bill)
        result = build_match(
            invoice,
            ctx,
            price_tolerance_pct=price_tolerance_pct,
            qty_tolerance_pct=qty_tolerance_pct,
            fuzzy_threshold=fuzzy_threshold,
            language=language,
        )
        logger.bind(
            po_name=ctx.name,
            verdict=result.verdict,
            lines=len(result.lines),
            reasons=result.reasons,
        ).info("invoice matched")
        _ = task, PoCandidate  # the task is read for its kind by the graph; kept for clarity
        return {**update, "match": result.model_dump(mode="json")}

    return match
