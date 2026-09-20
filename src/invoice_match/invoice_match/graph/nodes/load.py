"""First node: the invoice's source (an email with a PDF, or a bill typed in Odoo) and,
when the order is already known, the order."""

from __future__ import annotations

from typing import Any

from loguru import logger

from invoice_match.graph.nodes.common import fail, task_of
from invoice_match.graph.state import Node
from invoice_match.infra.ports import InvoicePorts
from sc_core.infra import tracing


def make_load_context(ports: InvoicePorts, *, max_attachment_chars: int = 12_000) -> Node:
    async def load_context(state: Any) -> dict[str, Any]:
        task = task_of(state)
        update: dict[str, Any] = {"po_context": None, "bill": None, "supplier_id": None}
        po_name = task.po_name
        if task.move_id is not None:
            bill = await ports.load_bill(task.move_id)
            if bill is None:
                return fail(f"bill {task.move_id} not found")
            update["bill"] = bill.model_dump(mode="json")
            update["supplier_id"] = bill.partner_id
            po_name = po_name or bill.po_name
            logger.bind(bill=bill.name or bill.move_id, lines=len(bill.lines)).info("bill loaded")
        if task.graph_message_id:
            meta = await ports.inbound_meta(task.graph_message_id)
            update["inbound_meta"] = meta.model_dump(mode="json")
            update["inbound_text"] = await ports.inbound_text(task.graph_message_id)
            update["attachments_text"] = await ports.attachments_text(
                task.graph_message_id, max_chars=max_attachment_chars
            )
            if meta.sender_address and update["supplier_id"] is None:
                update["supplier_id"] = await ports.partner_by_email(meta.sender_address)
            logger.bind(
                graph_message_id=task.graph_message_id,
                attachments=len(update["attachments_text"]),
            ).info("invoice email loaded")
        po_id = None
        if po_name:
            ctx = await ports.load_po(po_name)
            if ctx is None:
                return fail(f"purchase order {po_name} not found")
            update["po_context"] = ctx.model_dump(mode="json")
            update["supplier_id"] = update["supplier_id"] or ctx.partner_id
            po_id = ctx.id
        if state.get("run_id"):
            await ports.start_run(
                run_id=state["run_id"],
                case_id=state["case_id"],
                po_id=po_id,
                model=state.get("model"),
                trace_url=tracing.trace_url(state.get("trace_id")),
            )
        return update

    return load_context
