"""First node: the order and, by task, the supplier's notice or the receipt.

Email text lives in the state only while the run is active; terminal nodes
clear it.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from logistics.nodes.common import fail, task_of
from logistics.ports import LogisticsPorts
from logistics.state import Node
from sc_core.infra import tracing


def make_load_context(ports: LogisticsPorts, *, max_attachment_chars: int = 12_000) -> Node:
    async def load_context(state: Any) -> dict[str, Any]:
        task = task_of(state)
        update: dict[str, Any] = {"po_context": None, "receipt": None}
        po_name = task.po_name
        if task.kind in ("reconcile_receipt", "report_discrepancy"):
            assert task.picking_id is not None
            receipt = await ports.load_receipt(task.picking_id)
            if receipt is None:
                return fail(f"receipt {task.picking_id} not found")
            if not receipt.po_name:
                return fail(f"receipt {receipt.name} does not come from a purchase order")
            update["receipt"] = receipt.model_dump(mode="json")
            po_name = receipt.po_name
            logger.bind(picking=receipt.name, lines=len(receipt.lines)).info("receipt loaded")
        assert po_name is not None
        ctx = await ports.load_po(po_name)
        if ctx is None:
            return fail(f"purchase order {po_name} not found")
        update["po_context"] = ctx.model_dump(mode="json")
        if state.get("run_id"):
            await ports.start_run(
                run_id=state["run_id"],
                case_id=state["case_id"],
                po_id=ctx.id,
                model=state.get("model"),
                trace_url=tracing.trace_url(state.get("trace_id")),
            )
        if task.kind == "track_shipment":
            assert task.graph_message_id is not None
            meta = await ports.inbound_meta(task.graph_message_id)
            update["inbound_meta"] = meta.model_dump(mode="json")
            update["inbound_text"] = await ports.inbound_text(task.graph_message_id)
            update["attachments_text"] = await ports.attachments_text(
                task.graph_message_id, max_chars=max_attachment_chars
            )
            logger.bind(po_name=ctx.name, chars=len(update["inbound_text"])).info("notice loaded")
        return update

    return load_context
