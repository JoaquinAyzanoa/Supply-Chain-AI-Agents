"""Reconcile a receipt and, when it does not match, write to the supplier.

The comparison is arithmetic (``logistics.reconcile``); the model only
writes the email, in the supplier's language, from the discrepancy table
and the person's words when there are any.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from logistics.nodes.common import context_of, esc, fail, finish, receipt_of, task_of
from logistics.ports import LogisticsPorts
from logistics.reconcile import reconcile
from logistics.state import Node
from sc_core.i18n import Language, language_name, normalize, t
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.mail import po_token
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import ReceiptReconciliation
from supplier_comms.models import DraftOutput
from supplier_comms.nodes.common import style_tables
from supplier_comms.render import order_header

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def make_reconcile(
    ports: LogisticsPorts, *, tolerance_pct: float = 0.0, language: Language = "en"
) -> Node:
    async def reconcile_receipt(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        receipt = receipt_of(state)
        task = task_of(state)
        result = reconcile(receipt, tolerance_pct=tolerance_pct)
        update = {"reconciliation": result.model_dump(mode="json")}
        logger.bind(
            picking=receipt.name, lines=result.lines, discrepancies=len(result.discrepancies)
        ).info("receipt reconciled")
        if result.ok and task.kind == "reconcile_receipt":
            await ports.post_note(
                ctx.id,
                t("receipt.match_note", language, picking=esc(receipt.name), n=result.lines),
            )
            return {
                **update,
                **finish(
                    "no_action",
                    t(
                        "receipt.match_summary",
                        language,
                        picking=receipt.name,
                        po=ctx.name,
                        n=result.lines,
                    ),
                ),
            }
        return update

    return reconcile_receipt


def discrepancy_table(result: ReceiptReconciliation, language: Language) -> str:
    head = (
        f"<tr><th>{esc(t('receipt.col.product', language))}</th>"
        f"<th>{esc(t('receipt.col.expected', language))}</th>"
        f"<th>{esc(t('receipt.col.received', language))}</th>"
        f"<th>{esc(t('receipt.col.difference', language))}</th></tr>"
    )
    rows = "".join(
        f"<tr><td>{esc(d.product)}</td><td>{d.expected:g} {esc(d.uom or '')}</td>"
        f"<td>{d.received:g} {esc(d.uom or '')}</td>"
        f"<td>{d.difference:+g} ({esc(t(f'receipt.kind.{d.kind}', language))})</td></tr>"
        for d in result.discrepancies
    )
    return style_tables(f"<table><thead>{head}</thead><tbody>{rows}</tbody></table>")


def make_draft_discrepancy(
    chat: ChatCompleter,
    *,
    langfuse: LangfuseCfg | None,
    today: Callable[[], date],
    language: Language = "en",
) -> Node:
    async def draft_discrepancy(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        receipt = receipt_of(state)
        task = task_of(state)
        result = ReceiptReconciliation.model_validate(state["reconciliation"])
        if not ctx.supplier_emails:
            return fail(f"supplier {ctx.partner_name} has no email address in Odoo")
        email_lang = normalize(ctx.partner_lang, default=language)
        tone = get_prompt("supplier_tone", cfg=langfuse).compile(
            language=language_name(email_lang), signature=t("signature", email_lang)
        )
        formats = get_prompt("formats", cfg=langfuse).compile(po_name=ctx.name)
        prompt = get_prompt("draft_discrepancy", local_dir=PROMPTS_DIR, cfg=langfuse)
        context = order_header(ctx, today())
        context.append(
            f"Receipt {receipt.name}, {result.lines} lines, tolerance {result.tolerance_pct:g}%."
        )
        if result.discrepancies:
            context.append("Discrepancies (product, expected, received, kind):")
            context.extend(
                f"- {d.product}: expected {d.expected:g} {d.uom or ''}, received {d.received:g} "
                f"{d.uom or ''} ({d.kind})"
                for d in result.discrepancies
            )
        else:
            context.append("The counted quantities match the order.")
        if task.notes:
            context.append(f"What the receiving clerk says: {task.notes}")
        draft = await complete_structured(
            chat,
            [system("\n\n".join([tone, formats, prompt.text])), user("\n".join(context))],
            DraftOutput,
            name="logistics.draft_discrepancy",
            metadata={
                "prompt": prompt.name,
                "prompt_version": prompt.version,
                "po_name": ctx.name,
                "language": email_lang,
            },
        )
        logger.bind(po_name=ctx.name, picking=receipt.name).info("discrepancy email drafted")
        return {
            "outbound": {
                "kind": "discrepancy",
                "to": ctx.supplier_emails,
                "subject": po_token.tag_subject(draft.subject, ctx.name),
                "attachments": [],
            },
            "outbound_html": style_tables(draft.html_body),
        }

    return draft_discrepancy
