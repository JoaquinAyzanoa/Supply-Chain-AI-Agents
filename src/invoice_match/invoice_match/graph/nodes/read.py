"""Read the invoice into numbers: the model for a PDF or email, a plain copy for an Odoo bill.

A structured XML invoice (SUNAT UBL, for instance) would skip the model:
``parse_xml`` is the hook, unimplemented until the company receives one.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from invoice_match.graph.nodes.common import bill_of, task_of
from invoice_match.graph.state import Node
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import InvoiceData, InvoiceLine
from supplier_comms.domain.render import order_header

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def parse_xml(_text: str) -> InvoiceData | None:
    """Structured e-invoices (UBL/SUNAT XML) go here when the company receives them."""
    return None


def from_bill(bill: Any) -> InvoiceData:
    """A bill typed in Odoo already is structured data."""
    return InvoiceData(
        supplier_name=bill.partner_name,
        invoice_number=bill.ref or bill.name,
        invoice_date=bill.invoice_date,
        currency=bill.currency,
        po_reference=bill.po_name,
        lines=[
            InvoiceLine(
                description=line.description or "(no description)",
                product_ref=line.product_ref,
                qty=line.qty,
                unit_price=line.price_unit,
                total=line.total,
            )
            for line in bill.lines
        ],
        subtotal=bill.amount_untaxed,
        total=bill.amount_total,
        confidence=1.0,
    )


def make_read_invoice(
    chat: ChatCompleter, *, langfuse: LangfuseCfg | None, today: Callable[[], date]
) -> Node:
    async def read_invoice(state: Any) -> dict[str, Any]:
        task = task_of(state)
        bill = bill_of(state)
        if bill is not None and not task.graph_message_id:
            return {"invoice": from_bill(bill).model_dump(mode="json")}
        texts = list(state.get("attachments_text") or [])
        body = state.get("inbound_text") or ""
        for text in texts:
            parsed = parse_xml(text)
            if parsed is not None:
                return {"invoice": parsed.model_dump(mode="json")}
        prompt = get_prompt("extract_invoice", local_dir=PROMPTS_DIR, cfg=langfuse)
        context = [f"Today's date: {today().isoformat()}"]
        ctx = state.get("po_context")
        if ctx:
            from supplier_comms.domain.models import PoContext

            context.extend(order_header(PoContext.model_validate(ctx), today()))
        context.append("Supplier's email:")
        context.append(body.strip() or "(empty)")
        if texts:
            context.append("Text of the attachments:")
            context.extend(texts)
        data = await complete_structured(
            chat,
            [system(prompt.text), user("\n".join(context))],
            InvoiceData,
            name="invoice_match.extract_invoice",
            metadata={"prompt": prompt.name, "prompt_version": prompt.version},
        )
        logger.bind(
            number=data.invoice_number,
            lines=len(data.lines),
            total=data.total,
            confidence=data.confidence,
        ).info("invoice read")
        return {"invoice": data.model_dump(mode="json")}

    return read_invoice
