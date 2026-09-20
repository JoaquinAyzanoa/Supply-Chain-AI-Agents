"""A complaint or a dispute goes to a person, never to the model's pen (phase 11 S6)."""

from __future__ import annotations

from typing import Any

from loguru import logger

from sc_core.graph import ApprovalGateway, ApprovalRequest
from sc_core.i18n import Language, t
from sc_core.schema.autonomy import ActionFacts
from supplier_comms.graph.nodes.common import context_of, finish
from supplier_comms.graph.state import Node

DISPUTE_STEP = "dispute"


def make_dispute(approvals: ApprovalGateway, *, language: Language = "en") -> Node:
    async def dispute(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        classification = state.get("classification") or {}
        meta = state.get("inbound_meta") or {}
        reason = str(classification.get("reason") or "dispute")
        summary = t(
            "dispute.summary", language, partner=ctx.partner_name, po=ctx.name, reason=reason
        )
        update = await approvals.prepare(
            state,
            DISPUTE_STEP,
            ApprovalRequest(
                kind="escalation",
                summary=summary[:200],
                payload={
                    "po_name": ctx.name,
                    "reason": reason,
                    "classification": classification,
                    "sender_address": meta.get("sender_address"),
                    "web_link": meta.get("web_link"),
                    "graph_message_id": meta.get("graph_message_id"),
                },
                po_id=ctx.id,
                facts=ActionFacts(
                    partner_id=ctx.partner_id,
                    partner_name=ctx.partner_name,
                    amount=ctx.amount_total,
                    currency=ctx.currency,
                ),
            ),
        )
        pending = (update.get("pending_approvals") or [{}])[0]
        logger.bind(po_name=ctx.name).info("dispute escalated to a person")
        return {
            **update,
            **finish(
                "escalated",
                t("dispute.escalated", language, po=ctx.name, reason=reason),
                escalation_approval_id=pending.get("approval_id"),
            ),
        }

    return dispute
