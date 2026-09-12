"""Plain-text rendering of the order context for prompts and notes."""

from __future__ import annotations

from datetime import date
from typing import Any

from sc_core.i18n import Language, t
from sc_core.schema.a2a import SupplierCommsTask
from supplier_comms.models import PoContext


def lines_table(ctx: PoContext) -> str:
    rows = ["id | product | quantity | unit | unit price | currency | planned date | received"]
    for line in ctx.lines:
        rows.append(
            f"{line.id} | {line.product} | {line.qty:g} | {line.uom or '-'} | "
            f"{line.price_unit:.2f} | {line.currency or ctx.currency or '-'} | "
            f"{line.date_planned.isoformat() if line.date_planned else '-'} | {line.qty_received:g}"
        )
    return "\n".join(rows)


def order_header(ctx: PoContext, today: date) -> list[str]:
    return [
        f"Today's date: {today.isoformat()}",
        f"Order: {ctx.name} (state: {ctx.state}, id: {ctx.id})",
        f"Supplier: {ctx.partner_name} (partner_id: {ctx.partner_id})",
        f"Order planned date: {ctx.date_planned.isoformat() if ctx.date_planned else '-'}",
        f"Total amount: {ctx.amount_total:.2f} {ctx.currency or ''}".rstrip(),
        f"Previous emails on this order: {ctx.prior_mail_links}",
        "Lines:",
        lines_table(ctx),
    ]


def outbound_context(task: SupplierCommsTask, ctx: PoContext, today: date) -> str:
    parts = order_header(ctx, today)
    if task.days_silent is not None:
        parts.append(f"Days without a supplier reply: {task.days_silent}")
    if task.notes:
        parts.append(f"Buyer's instructions: {task.notes}")
    return "\n".join(parts)


def inbound_context(
    ctx: PoContext, today: date, inbound_text: str, attachments_text: list[str]
) -> str:
    parts = order_header(ctx, today)
    parts.append("Supplier's email:")
    parts.append(inbound_text.strip() or "(empty)")
    if attachments_text:
        parts.append("Text of the attachments:")
        parts.extend(attachments_text)
    return "\n".join(parts)


def reply_context(task: SupplierCommsTask, ctx: PoContext, today: date, inbound_text: str) -> str:
    parts = order_header(ctx, today)
    parts.append("Supplier's question (received email):")
    parts.append(inbound_text.strip() or "(empty)")
    if task.notes:
        parts.append(f"Buyer's instructions: {task.notes}")
    return "\n".join(parts)


def changes_html(changes: list[dict[str, Any]], language: Language = "en") -> str:
    rows = []
    label = t("changes.review_label", language)
    for c in changes:
        review = f" ({label}: {_esc(str(c.get('review_reason')))})" if c.get("needs_review") else ""
        rows.append(
            f"<li>{_esc(str(c.get('product')))} · {_esc(str(c.get('field')))}: "
            f"{_esc(str(c.get('before') or '-'))} → {_esc(str(c.get('after')))}{review}</li>"
        )
    return f"<ul>{''.join(rows)}</ul>"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
