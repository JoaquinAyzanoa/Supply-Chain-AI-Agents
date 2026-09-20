"""Write the verdict on the bill itself, so Odoo's invoice form shows it.

The "AI Agent" tab on a vendor bill reads ``sc_match_verdict``, the matched
order, a short summary and when it was checked. A bill typed in Odoo gets
them as soon as the match is known, before anyone decides; a bill the agent
drafts gets them right after it is created (see ``decide.make_apply``).
"""

from __future__ import annotations

from typing import Any

from invoice_match.graph.nodes.common import bill_of, context_of
from invoice_match.graph.state import Node
from invoice_match.infra.ports import InvoicePorts
from sc_core.i18n import Language, t
from sc_core.schema.a2a import BillMatch


def check_summary(match: BillMatch, language: Language) -> str:
    """One paragraph for the invoice form: what matched, or what did not and why."""
    if match.verdict == "clean":
        return t("bill.check_clean", language, n=len(match.lines))
    return t("bill.check_hold", language, reasons="; ".join(match.reasons) or "-")


def make_record_check(ports: InvoicePorts, *, language: Language = "en") -> Node:
    async def record_check(state: Any) -> dict[str, Any]:
        bill = bill_of(state)
        if bill is None:
            return {}
        match = BillMatch.model_validate(state["match"])
        await ports.record_check(
            bill.move_id,
            verdict=match.verdict,
            po_id=context_of(state).id,
            summary=check_summary(match, language),
        )
        return {}

    return record_check
