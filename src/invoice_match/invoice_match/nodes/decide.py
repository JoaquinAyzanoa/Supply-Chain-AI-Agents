"""The ``vendor_bill`` approval and what an approval unlocks.

Clean invoice: the person (or the amount rule) approves and a draft bill is
created from the order; a bill already typed in Odoo only gets its match
note. Held invoice: the same approval carries the variance table; approving
means "create it anyway", rejecting leaves the invoice with the supplier.
Nothing is ever posted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from invoice_match.nodes.common import bill_of, context_of, esc, finish, invoice_of, style_tables
from invoice_match.ports import InvoicePorts
from invoice_match.state import Node
from sc_core.graph import ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.schema.a2a import BillMatch

BILL_STEP = "vendor_bill"


def make_bill_approval(
    *, auto_approve_amount: float = 0.0, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        ctx = context_of(state)
        invoice = invoice_of(state)
        match = BillMatch.model_validate(state["match"])
        bill = bill_of(state)
        amount = invoice.total if invoice.total is not None else invoice.subtotal or 0.0
        auto = (
            match.verdict == "clean" and auto_approve_amount > 0 and amount <= auto_approve_amount
        )
        key = "bill.approval_clean" if match.verdict == "clean" else "bill.approval_hold"
        return ApprovalRequest(
            kind="vendor_bill",
            summary=t(
                key,
                language,
                number=invoice.invoice_number or "-",
                partner=ctx.partner_name,
                po=ctx.name,
                amount=f"{amount:.2f} {invoice.currency or ctx.currency or ''}".strip(),
            )[:200],
            payload={
                "po_name": ctx.name,
                "verdict": match.verdict,
                "reasons": match.reasons,
                "invoice": invoice.model_dump(mode="json"),
                "lines": [line.model_dump(mode="json") for line in match.lines],
                "expected_subtotal": match.expected_subtotal,
                "existing_bill_id": bill.move_id if bill else None,
                "existing_bill_name": bill.name if bill else None,
            },
            po_id=ctx.id,
            auto_approve=auto,
            auto_reason=t("bill.auto_reason", language, amount=auto_approve_amount)
            if auto
            else None,
        )

    return build


def match_html(match: BillMatch, language: Language) -> str:
    head = "".join(
        f"<th>{esc(t(f'bill.col.{col}', language))}</th>"
        for col in ("product", "billed", "price", "ordered", "received", "status")
    )
    rows = "".join(
        "<tr>"
        f"<td>{esc(line.product or line.invoice_description)}</td>"
        f"<td>{line.invoice_qty if line.invoice_qty is not None else '-'}</td>"
        f"<td>{line.invoice_price if line.invoice_price is not None else '-'}</td>"
        f"<td>{line.po_qty if line.po_qty is not None else '-'} @ "
        f"{line.po_price if line.po_price is not None else '-'}</td>"
        f"<td>{line.received_qty if line.received_qty is not None else '-'}</td>"
        f"<td>{esc(t(f'bill.status.{line.status}', language))}"
        f"{' · ' + esc(line.note) if line.note else ''}</td>"
        "</tr>"
        for line in match.lines
    )
    return style_tables(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")


def make_apply(ports: InvoicePorts, *, language: Language = "en") -> Node:
    async def apply(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        invoice = invoice_of(state)
        match = BillMatch.model_validate(state["match"])
        bill = bill_of(state)
        decision = decision_for(state, BILL_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        if bill is not None:
            await ports.post_note(
                ctx.id,
                t(
                    "bill.checked_note",
                    language,
                    bill=esc(bill.name or str(bill.move_id)),
                    verdict=esc(t(f"bill.verdict.{match.verdict}", language)),
                    who=esc(who),
                )
                + match_html(match, language),
            )
            return finish(
                "applied",
                t("bill.checked_summary", language, bill=bill.name or bill.move_id, po=ctx.name),
                created_bill={"id": bill.move_id, "name": bill.name},
            )
        bill_id, bill_name = await ports.create_draft_bill(
            ctx.id, ref=invoice.invoice_number, invoice_date=invoice.invoice_date
        )
        await ports.post_note(
            ctx.id,
            t(
                "bill.created_note",
                language,
                number=esc(invoice.invoice_number or "-"),
                bill=esc(bill_name or str(bill_id)),
                who=esc(who),
                verdict=esc(t(f"bill.verdict.{match.verdict}", language)),
            )
            + match_html(match, language),
        )
        logger.bind(po_name=ctx.name, bill_id=bill_id, verdict=match.verdict).info(
            "draft bill created"
        )
        return finish(
            "applied",
            t(
                "bill.created_summary",
                language,
                number=invoice.invoice_number or "-",
                bill=bill_name or bill_id,
                po=ctx.name,
            ),
            created_bill={"id": bill_id, "name": bill_name},
        )

    return apply


def make_rejected(ports: InvoicePorts, *, language: Language = "en") -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        invoice = invoice_of(state)
        decision = decision_for(state, BILL_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        await ports.post_note(
            ctx.id,
            t(
                "bill.rejected_note",
                language,
                number=esc(invoice.invoice_number or "-"),
                who=esc(who),
                reason=esc(reason),
            ),
        )
        return finish(
            "rejected",
            t(
                "bill.rejected_summary",
                language,
                number=invoice.invoice_number or "-",
                who=who,
                reason=reason,
            ),
        )

    return rejected
