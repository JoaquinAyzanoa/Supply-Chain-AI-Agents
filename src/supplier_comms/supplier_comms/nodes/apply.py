"""The ``po_change`` approval and the writes it unlocks.

Only changes without ``needs_review`` are written; the reviewed ones stay in
the approval payload and the chatter note for a person to act on. Every
write goes through repositories that leave Odoo's own audit trail
(``sc_log_eta_change`` on the order, price list rows).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from loguru import logger

from sc_core.graph import ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.schema.a2a import ChangeProposal
from supplier_comms.nodes.common import context_of, esc, finish
from supplier_comms.ports import AgentPorts
from supplier_comms.render import changes_html
from supplier_comms.state import Node

CHANGE_STEP = "po_change"


def make_change_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        ctx = context_of(state)
        proposal = ChangeProposal.model_validate(state["proposal"])
        review = sum(1 for c in proposal.changes if c.needs_review)
        return ApprovalRequest(
            kind="po_change",
            summary=t("changes.approval_summary", language, po=ctx.name, summary=proposal.summary),
            payload={
                "po_name": ctx.name,
                "summary": proposal.summary,
                "changes": [c.model_dump(mode="json") for c in proposal.changes],
                "needs_review": review,
                "classification": state.get("classification"),
            },
            po_id=ctx.id,
        )

    return build


def make_apply_changes(ports: AgentPorts, *, language: Language = "en") -> Node:
    async def apply_changes(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        proposal = ChangeProposal.model_validate(state["proposal"])
        run_id = state.get("run_id") or "run_unknown"
        lines = {line.id: line for line in ctx.lines}
        decision = decision_for(state, CHANGE_STEP)
        # the approver may have unticked lines in the Control Tower
        accepted = (decision.details or {}).get("accepted_line_ids") if decision else None
        wanted = {int(i) for i in accepted} if accepted is not None else None
        applied: list[dict[str, Any]] = []
        confidences: list[float] = []
        for change in proposal.applicable:
            line = lines.get(change.po_line_id)
            if line is None or (wanted is not None and line.id not in wanted):
                continue
            if change.field == "date_planned":
                await ports.set_line_date(line.id, date.fromisoformat(change.after), run_id=run_id)
                confidences.append(change.confidence)
            elif change.field == "price" and line.product_tmpl_id and ctx.currency_id:
                await ports.upsert_price(
                    partner_id=ctx.partner_id,
                    product_tmpl_id=line.product_tmpl_id,
                    product_id=line.product_id,
                    price=float(change.after.split()[0]),
                    currency_id=ctx.currency_id,
                    min_qty=0.0,
                    lead_days=_lead_for(proposal, line.id),
                )
            elif change.field == "lead_days":
                if not any(
                    c.field == "price" and c.po_line_id == line.id for c in proposal.applicable
                ):
                    if line.product_tmpl_id and ctx.currency_id:
                        await ports.upsert_price(
                            partner_id=ctx.partner_id,
                            product_tmpl_id=line.product_tmpl_id,
                            product_id=line.product_id,
                            price=line.price_unit,
                            currency_id=ctx.currency_id,
                            min_qty=0.0,
                            lead_days=int(change.after),
                        )
            applied.append(change.model_dump(mode="json"))
        if confidences:
            await ports.set_eta_meta(ctx.id, confidence=min(confidences))
        skipped = [
            c.model_dump(mode="json")
            for c in proposal.changes
            if c.needs_review or (wanted is not None and c.po_line_id not in wanted)
        ]
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        await ports.post_note(
            ctx.id,
            t("changes.applied_note", language, n=len(applied), who=esc(who))
            + changes_html(applied, language)
            + (
                t("changes.pending_note", language) + changes_html(skipped, language)
                if skipped
                else ""
            ),
        )
        logger.bind(po_name=ctx.name, applied=len(applied), skipped=len(skipped)).info(
            "changes applied"
        )
        return finish(
            "applied",
            t("changes.applied_summary", language, n=len(applied), po=ctx.name)
            + (t("changes.pending_summary", language, n=len(skipped)) if skipped else ""),
        )

    return apply_changes


def make_change_rejected(ports: AgentPorts, *, language: Language = "en") -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        decision = decision_for(state, CHANGE_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        await ports.post_note(
            ctx.id,
            t("changes.rejected_note", language, who=esc(who), reason=esc(reason)),
        )
        return finish("rejected", t("changes.rejected_summary", language, who=who, reason=reason))

    return rejected


def make_no_action(*, language: Language = "en") -> Node:
    async def no_action(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        classification = state.get("classification") or {}
        return finish(
            "no_action",
            t(
                "changes.no_action",
                language,
                po=ctx.name,
                kind=classification.get("kind", "other"),
                reason=classification.get("reason", "no action"),
            ),
        )

    return no_action


def _lead_for(proposal: ChangeProposal, line_id: int) -> int | None:
    for c in proposal.applicable:
        if c.field == "lead_days" and c.po_line_id == line_id:
            return int(c.after)
    return None
