"""First node: the order, its lines, the supplier, the mail history and, for
inbound tasks, the supplier's text (body and PDF attachments) fetched from Graph.

The text lives in the state only while the run is active; terminal nodes
clear it.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from sc_core.infra import tracing
from sc_core.schema.a2a import PriceListDiff
from supplier_comms.domain.models import InboundMeta, PoContext
from supplier_comms.domain.pricelists import build_diff, parse_price_list
from supplier_comms.graph.nodes.common import fail, task_of
from supplier_comms.graph.state import Node
from supplier_comms.infra.ports import AgentPorts

PRICE_LIST_TASKS = ("handle_inbound", "resolve_unlinked")


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
        if state.get("run_id"):
            po_ctx = update["po_context"]
            await ports.start_run(
                run_id=state["run_id"],
                case_id=state["case_id"],
                po_id=po_ctx["id"] if po_ctx else None,
                model=state.get("model"),
                trace_url=tracing.trace_url(state.get("trace_id")),
            )
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
            if meta.has_attachments and task.kind in PRICE_LIST_TASKS:
                ctx_obj = (
                    PoContext.model_validate(update["po_context"]) if update["po_context"] else None
                )
                diff = await price_list_diff(ports, task.graph_message_id, meta, ctx_obj)
                if diff is not None:
                    update["price_list"] = diff.model_dump(mode="json")
        return update

    return load_context


async def price_list_diff(
    ports: AgentPorts, message_id: str, meta: InboundMeta, ctx: PoContext | None
) -> PriceListDiff | None:
    """A spreadsheet that reads as a price list, diffed against the sender's prices.

    The sender must be a known supplier (the order's, or the partner behind the
    address); a stranger's spreadsheet is left to the normal flow.
    """
    tables = await ports.attachment_tables(message_id)
    parsed = next((p for p in (parse_price_list(t) for t in tables) if p is not None), None)
    if parsed is None:
        return None
    partner_id = ctx.partner_id if ctx else None
    partner_name = ctx.partner_name if ctx else ""
    if partner_id is None and meta.sender_address:
        partner_id = await ports.partner_by_email(meta.sender_address)
    if partner_id is None:
        logger.bind(graph_message_id=message_id).info("price list from a stranger; not diffed")
        return None
    current = await ports.price_history(partner_id, None)
    resolved: dict[str, tuple[int, str] | None] = {}

    async def resolve(code: str) -> tuple[int, str] | None:
        key = code.strip().upper()
        if key not in resolved:
            resolved[key] = await ports.product_by_code(key)
        return resolved[key]

    for row in parsed.rows:
        await resolve(row.code)
    diff = build_diff(
        parsed,
        partner_id=partner_id,
        partner_name=partner_name,
        current=current,
        lookup=lambda code: resolved.get(code.strip().upper()),
    )
    if not any(r.matched for r in diff.rows):
        logger.bind(rows=len(diff.rows)).info("price list rows match no product; ignored")
        return None
    logger.bind(
        partner_id=partner_id, rows=len(diff.rows), unmatched=diff.unmatched, source=diff.source
    ).info("price list read from the attachment")
    return diff
