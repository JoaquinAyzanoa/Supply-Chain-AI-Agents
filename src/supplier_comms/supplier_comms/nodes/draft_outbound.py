"""Draft an RFQ, an ETA request or a follow-up with the model and read-only tools.

Two model phases: a tool loop where the model gathers what it needs, then a
structured completion over the same conversation that yields the subject
and HTML body. The subject gets the order token; headers get the order and
case ids so replies link by rule.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from sc_core.graph import ToolBox
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.llm.tool_loop import run_tool_loop, tool_exchange_summary
from sc_core.mail import po_token
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import DraftKind, OutboundDraft
from supplier_comms.models import DraftOutput
from supplier_comms.nodes.common import context_of, fail, task_of
from supplier_comms.render import outbound_context, reply_context
from supplier_comms.state import Node

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

KIND_TO_DRAFT: dict[str, DraftKind] = {
    "send_rfq": "rfq",
    "request_eta": "request_eta",
    "follow_up": "follow_up",
    "send_po": "send_po",
}

FINAL_INSTRUCTION = (
    "Ahora entrega el correo definitivo como JSON con las claves subject y html_body. "
    "El html_body debe ser HTML sencillo (p, table, ul) sin estilos ni scripts."
)


def make_draft_outbound(
    chat: ChatCompleter,
    toolbox: ToolBox,
    *,
    max_tool_rounds: int,
    langfuse: LangfuseCfg | None,
    today: Callable[[], date],
) -> Node:
    async def draft_outbound(state: Any) -> dict[str, Any]:
        task = task_of(state)
        ctx = context_of(state)
        kind = KIND_TO_DRAFT.get(task.kind, "reply")  # inbound questions are answered in-thread
        if not ctx.supplier_emails:
            return fail(f"supplier {ctx.partner_name} has no email address in Odoo")
        if kind == "send_po" and ctx.state not in ("purchase", "done"):
            return fail(f"{ctx.name} is not a confirmed order (state {ctx.state}); nothing to send")

        tone = get_prompt("supplier_tone", cfg=langfuse)
        formats = get_prompt("formats", cfg=langfuse)
        prompt = get_prompt(f"draft_{kind}", local_dir=PROMPTS_DIR, cfg=langfuse)
        system_text = "\n\n".join([tone.text, formats.compile(po_name=ctx.name), prompt.text])
        metadata = {"prompt": prompt.name, "prompt_version": prompt.version, "po_name": ctx.name}

        if kind == "reply":
            context_text = reply_context(task, ctx, today(), state.get("inbound_text") or "")
        else:
            context_text = outbound_context(task, ctx, today())
        loop = await run_tool_loop(
            chat,
            [system(system_text), user(context_text)],
            toolbox,
            max_rounds=max_tool_rounds,
            name=f"supplier_comms.draft_{kind}",
            metadata=metadata,
        )
        draft = await complete_structured(
            chat,
            [*loop.messages(), user(FINAL_INSTRUCTION)],
            DraftOutput,
            name=f"supplier_comms.draft_{kind}.final",
            metadata=metadata,
        )
        outbound = OutboundDraft(
            kind=kind,
            to=ctx.supplier_emails,
            subject=po_token.tag_subject(draft.subject, ctx.name),
            html_body=draft.html_body,
            reply_to_message_id=task.graph_message_id if kind == "reply" else None,
        )
        logger.bind(po_name=ctx.name, kind=kind, tool_calls=loop.tool_calls).info("draft ready")
        attachments = [f"{ctx.name}.pdf"] if kind == "send_po" else []
        return {
            "outbound": {
                **outbound.model_dump(mode="json", exclude={"html_body"}),
                "attachments": attachments,
            },
            "outbound_html": outbound.html_body,
            "tool_exchange": tool_exchange_summary(loop.history),
        }

    return draft_outbound
