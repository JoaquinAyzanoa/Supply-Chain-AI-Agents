"""Answer a supplier's question from our records, citing them (phase 11 S6).

The model reads the order, its terms and the price history through the
read-only tools and returns the reply together with a verdict: factual (every
part of the question is answered by a record, sources listed) or not (a
decision is needed). A factual answer is an ``answer`` email the autonomy
policy may send alone; anything else is a ``reply`` that a person reads
first, whatever the rules say. Disputes never reach this node: ``classify``
sends them to ``dispute``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from sc_core.graph import ToolBox
from sc_core.i18n import Language, language_name, normalize, t
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.llm.tool_loop import run_tool_loop, tool_exchange_summary
from sc_core.mail import po_token
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import AnswerSummary, DraftKind, OutboundDraft
from sc_core.schema.profiles import profile_lines
from supplier_comms.domain.models import AnswerOutput
from supplier_comms.domain.render import reply_context
from supplier_comms.graph.nodes.common import context_of, fail, style_tables, task_of
from supplier_comms.graph.state import Node
from supplier_comms.infra.ports import AgentPorts

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

FINAL_INSTRUCTION = (
    "Now deliver the final answer as JSON with the keys subject, html_body, factual, "
    "sources and reason. The html_body must be simple HTML (p, table, ul) without styles "
    "or scripts."
)


def make_answer(
    chat: ChatCompleter,
    toolbox: ToolBox,
    ports: AgentPorts,
    *,
    max_tool_rounds: int,
    langfuse: LangfuseCfg | None,
    today: Callable[[], date],
    language: Language = "en",
) -> Node:
    async def answer(state: Any) -> dict[str, Any]:
        task = task_of(state)
        ctx = context_of(state)
        if not ctx.supplier_emails:
            return fail(f"supplier {ctx.partner_name} has no email address in Odoo")
        profile = await ports.supplier_profile(ctx.partner_id)
        # the supplier's language: the profile people keep wins over the Odoo partner
        email_lang = normalize(
            (profile.language if profile else None) or ctx.partner_lang, default=language
        )
        tone = get_prompt("supplier_tone", cfg=langfuse).compile(
            language=language_name(email_lang), signature=t("signature", email_lang)
        )
        formats = get_prompt("formats", cfg=langfuse)
        prompt = get_prompt("answer_question", local_dir=PROMPTS_DIR, cfg=langfuse)
        system_text = "\n\n".join([tone, formats.compile(po_name=ctx.name), prompt.text])
        metadata = {
            "prompt": prompt.name,
            "prompt_version": prompt.version,
            "po_name": ctx.name,
            "language": email_lang,
        }
        context_text = reply_context(task, ctx, today(), state.get("inbound_text") or "")
        lines = profile_lines(profile)
        if lines:
            context_text = context_text + "\n" + "\n".join(lines)
        loop = await run_tool_loop(
            chat,
            [system(system_text), user(context_text)],
            toolbox,
            max_rounds=max_tool_rounds,
            name="supplier_comms.answer",
            metadata=metadata,
        )
        output = await complete_structured(
            chat,
            [*loop.messages(), user(FINAL_INSTRUCTION)],
            AnswerOutput,
            name="supplier_comms.answer.final",
            metadata=metadata,
        )
        factual = output.factual and bool(output.sources)
        kind: DraftKind = "answer" if factual else "reply"
        outbound = OutboundDraft(
            kind=kind,
            to=ctx.supplier_emails,
            subject=po_token.tag_subject(output.subject, ctx.name),
            html_body=style_tables(output.html_body),
            reply_to_message_id=task.graph_message_id,
        )
        summary = AnswerSummary(
            factual=factual,
            sources=[s[:200] for s in output.sources][:20],
            reason=output.reason if not factual else None,
        )
        logger.bind(
            po_name=ctx.name, factual=factual, sources=len(summary.sources), tools=loop.tool_calls
        ).info("question answered" if factual else "question needs a person")
        return {
            "outbound": {
                **outbound.model_dump(mode="json", exclude={"html_body"}),
                "attachments": [],
            },
            "outbound_html": outbound.html_body,
            "tool_exchange": tool_exchange_summary(loop.history),
            "answer": summary.model_dump(mode="json"),
            # a non-factual answer is read by a person whatever the autonomy rules say
            "force_approval": not factual,
        }

    return answer
