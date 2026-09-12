"""First node: the order, its lines, the supplier, the mail history and, for
inbound tasks, the supplier's text (body and PDF attachments) fetched from Graph.

The text lives in the state only while the run is active; terminal nodes
clear it.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from supplier_comms.nodes.common import fail, task_of
from supplier_comms.ports import AgentPorts
from supplier_comms.state import Node


def make_load_context(ports: AgentPorts, *, max_attachment_chars: int = 12_000) -> Node:
    async def load_context(state: Any) -> dict[str, Any]:
        task = task_of(state)
        update: dict[str, Any] = {"po_context": None}
        if task.po_name:
            ctx = await ports.load_po(task.po_name)
            if ctx is None:
                return fail(f"purchase order {task.po_name} not found")
            update["po_context"] = ctx.model_dump(mode="json")
            logger.bind(po_name=ctx.name, lines=len(ctx.lines)).info("context loaded")
        if task.graph_message_id:
            meta = await ports.inbound_meta(task.graph_message_id)
            update["inbound_meta"] = meta.model_dump(mode="json")
            update["inbound_text"] = await ports.inbound_text(task.graph_message_id)
            update["attachments_text"] = await ports.attachments_text(
                task.graph_message_id, max_chars=max_attachment_chars
            )
            logger.bind(
                graph_message_id=task.graph_message_id,
                chars=len(update["inbound_text"]),
                attachments=len(update["attachments_text"]),
            ).info("inbound text loaded")
        return update

    return load_context
