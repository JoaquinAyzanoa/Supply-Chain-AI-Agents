"""First node: the order, its lines, the supplier and the mail history."""

from __future__ import annotations

from typing import Any

from loguru import logger

from supplier_comms.nodes.common import fail, task_of
from supplier_comms.ports import AgentPorts
from supplier_comms.state import Node


def make_load_context(ports: AgentPorts) -> Node:
    async def load_context(state: Any) -> dict[str, Any]:
        task = task_of(state)
        if not task.po_name:
            return {"po_context": None}
        ctx = await ports.load_po(task.po_name)
        if ctx is None:
            return fail(f"purchase order {task.po_name} not found")
        logger.bind(po_name=ctx.name, lines=len(ctx.lines)).info("context loaded")
        return {"po_context": ctx.model_dump(mode="json")}

    return load_context
