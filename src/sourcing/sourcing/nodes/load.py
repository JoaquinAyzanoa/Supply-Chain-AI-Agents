"""First node: the order (if any), the basket and, for a comparison, the round."""

from __future__ import annotations

from typing import Any

from loguru import logger

from sc_core.infra import tracing
from sourcing.nodes.common import fail, task_of
from sourcing.ports import SourcingPorts
from sourcing.state import Node


def make_load(ports: SourcingPorts) -> Node:
    async def load(state: Any) -> dict[str, Any]:
        task = task_of(state)
        if state.get("run_id"):
            await ports.start_run(
                run_id=state["run_id"],
                case_id=state["case_id"],
                model=state.get("model"),
                trace_url=tracing.trace_url(state.get("trace_id")),
            )
        update: dict[str, Any] = {"order": None, "basket": [], "round": None}
        if task.po_name:
            order = await ports.order(task.po_name)
            if order is None:
                return fail(f"order {task.po_name} not found in Odoo")
            update["order"] = order.model_dump(mode="json")
            update["basket"] = [
                b.model_dump(mode="json") for b in await ports.basket_for_po(task.po_name)
            ]
        elif task.product_id and task.qty:
            update["basket"] = [
                b.model_dump(mode="json")
                for b in await ports.basket_for_product(task.product_id, task.qty)
            ]
        if task.kind == "compare_quotes":
            found = None
            if task.round_id:
                found = await ports.get_round(task.round_id)
            if found is None and task.po_name:
                found = await ports.open_round_for_po(task.po_name)
            if found is None:
                found = await ports.round_for_case(task.case_id)
            if found is None:
                return fail("no quote round to compare")
            update["round"] = found.model_dump(mode="json")
            update["basket"] = [b.model_dump(mode="json") for b in found.basket]
        elif task.kind in ("quote_round", "alternate_source") and not update["basket"]:
            return fail("nothing to source: the order has no product lines")
        logger.bind(kind=task.kind, lines=len(update["basket"])).info("context loaded")
        return update

    return load
