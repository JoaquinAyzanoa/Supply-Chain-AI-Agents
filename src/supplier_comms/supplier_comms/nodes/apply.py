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
from sc_core.schema.a2a import ChangeProposal
from supplier_comms.nodes.common import context_of, esc, finish
from supplier_comms.ports import AgentPorts
from supplier_comms.render import changes_html
from supplier_comms.state import Node

CHANGE_STEP = "po_change"


def make_change_approval() -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        ctx = context_of(state)
        proposal = ChangeProposal.model_validate(state["proposal"])
        review = sum(1 for c in proposal.changes if c.needs_review)
        return ApprovalRequest(
            kind="po_change",
            summary=f"Cambios propuestos en {ctx.name}: {proposal.summary}",
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


def make_apply_changes(ports: AgentPorts) -> Node:
    async def apply_changes(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        proposal = ChangeProposal.model_validate(state["proposal"])
        run_id = state.get("run_id") or "run_unknown"
        lines = {line.id: line for line in ctx.lines}
        applied: list[dict[str, Any]] = []
        confidences: list[float] = []
        for change in proposal.applicable:
            line = lines.get(change.po_line_id)
            if line is None:
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
        skipped = [c.model_dump(mode="json") for c in proposal.changes if c.needs_review]
        decision = decision_for(state, CHANGE_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        await ports.post_note(
            ctx.id,
            f"<p>Agente supplier_comms aplicó {len(applied)} cambio(s) aprobado(s) por {esc(who)} "
            f"(caso {esc(state['case_id'])}, run {esc(run_id)}).</p>"
            + changes_html(applied)
            + (f"<p>Pendientes de revisión manual:</p>{changes_html(skipped)}" if skipped else ""),
        )
        logger.bind(po_name=ctx.name, applied=len(applied), skipped=len(skipped)).info(
            "changes applied"
        )
        return finish(
            "applied",
            f"{len(applied)} cambio(s) aplicado(s) en {ctx.name}"
            + (f", {len(skipped)} pendiente(s) de revisión" if skipped else ""),
        )

    return apply_changes


def make_change_rejected(ports: AgentPorts) -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        decision = decision_for(state, CHANGE_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = decision.reason if decision and decision.reason else "sin motivo"
        await ports.post_note(
            ctx.id,
            f"<p>Cambios propuestos rechazados por {esc(who)}: {esc(reason)} "
            f"(caso {esc(state['case_id'])}).</p>",
        )
        return finish("rejected", f"cambios rechazados por {who}: {reason}")

    return rejected


def make_no_action() -> Node:
    async def no_action(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        classification = state.get("classification") or {}
        return finish(
            "no_action",
            f"{ctx.name}: correo clasificado como {classification.get('kind', 'other')}; "
            f"{classification.get('reason', 'sin acción')}",
        )

    return no_action


def _lead_for(proposal: ChangeProposal, line_id: int) -> int | None:
    for c in proposal.applicable:
        if c.field == "lead_days" and c.po_line_id == line_id:
            return int(c.after)
    return None
