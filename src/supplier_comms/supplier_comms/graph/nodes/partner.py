"""A new supplier from an unknown sender's quotation (phase 11).

``resolve_unlinked`` found no order for the message and the sender is not a
partner. When the text reads as a quotation for products we buy, the run
pauses on a ``partner_create`` approval that shows the sender, a suggested
company name and the quoted lines. Approved, the partner is created in
Odoo, an RFQ carries the quoted lines (those whose reference matches a
product code) and the message is linked to it, so the quote can enter a
round. Nothing is created without the approval.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from sc_core.graph import ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.schema.a2a import QuotationData
from sc_core.schema.autonomy import ActionFacts
from supplier_comms.domain.models import InboundMeta
from supplier_comms.graph.nodes.common import finish
from supplier_comms.graph.state import Node
from supplier_comms.infra.ports import AgentPorts

PARTNER_STEP = "partner_create"


def suggested_name(address: str | None) -> str:
    """``ventas@aceros-lima.com`` -> ``Aceros Lima``; a bare address keeps its local part."""
    if not address or "@" not in address:
        return "New supplier"
    domain = address.split("@", 1)[1].lower()
    label = domain.split(".")[0]
    if label in ("gmail", "hotmail", "outlook", "yahoo", "live", "icloud"):
        label = address.split("@", 1)[0]
    words = [w for w in label.replace("_", "-").replace(".", "-").split("-") if w]
    return " ".join(w.capitalize() for w in words) or "New supplier"


def candidate_from(meta: InboundMeta, data: QuotationData) -> dict[str, Any] | None:
    """The state entry for the approval, or None when nothing was quoted."""
    priced = [line for line in data.lines if line.unit_price]
    if not priced:
        return None
    return {
        "sender_address": meta.sender_address,
        "graph_message_id": meta.graph_message_id,
        "subject_token": meta.subject_token,
        "web_link": meta.web_link,
        "suggested_name": suggested_name(meta.sender_address),
        "quotation": data.model_dump(mode="json"),
    }


def make_partner_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        candidate = state.get("partner_candidate") or {}
        quotation = QuotationData.model_validate(candidate.get("quotation") or {})
        return ApprovalRequest(
            kind="partner_create",
            summary=t(
                "partner.approval_summary",
                language,
                sender=candidate.get("sender_address") or "-",
                n=len([ln for ln in quotation.lines if ln.unit_price]),
            )[:200],
            payload={
                **{k: v for k, v in candidate.items() if k != "quotation"},
                "lines": [ln.model_dump(mode="json") for ln in quotation.lines],
                "currency": quotation.currency,
                "notes": quotation.notes,
                "writes": "a vendor partner and an RFQ with the quoted lines",
            },
            res_model="sc.approval",
            res_id=None,
            review_on_approval=True,
            facts=ActionFacts(first_time_supplier=True),
        )

    return build


def make_create_partner(ports: AgentPorts, *, language: Language = "en") -> Node:
    async def create_partner(state: Any) -> dict[str, Any]:
        candidate = state.get("partner_candidate") or {}
        quotation = QuotationData.model_validate(candidate.get("quotation") or {})
        decision = decision_for(state, PARTNER_STEP)
        details = (decision.details or {}) if decision else {}
        name = str(details.get("name") or candidate.get("suggested_name") or "New supplier")
        email = str(details.get("email") or candidate.get("sender_address") or "") or None
        partner_id, partner_name = await ports.create_supplier(name, email)
        lines: list[dict[str, Any]] = []
        unmatched: list[str] = []
        for line in quotation.lines:
            if not line.unit_price:
                continue
            product = await ports.product_by_code(line.product_ref) if line.product_ref else None
            if product is None:
                unmatched.append(line.product_ref or line.description[:40])
                continue
            lines.append(
                {
                    "product_id": product[0],
                    "product_qty": line.qty or line.min_qty or 1.0,
                    "price_unit": line.unit_price,
                }
            )
        po_name: str | None = None
        if lines:
            po_id, po_name = await ports.create_rfq(
                partner_id,
                lines,
                external_ref=f"newsupplier-{state['case_id']}",
                origin=t("partner.rfq_origin", language, sender=email or partner_name),
            )
            meta = InboundMeta.model_validate(state["inbound_meta"])
            await ports.link_inbound(po_id, meta, case_id=state["case_id"])
            await ports.post_note(
                po_id, t("partner.rfq_note", language, partner=partner_name, n=len(lines))
            )
        logger.bind(partner_id=partner_id, po_name=po_name, unmatched=len(unmatched)).info(
            "new supplier created"
        )
        return finish(
            "applied",
            t(
                "partner.created_summary",
                language,
                partner=partner_name,
                po=po_name or "-",
                n=len(lines),
                unmatched=", ".join(unmatched) or "-",
            ),
            partner_candidate=None,
        )

    return create_partner


def make_partner_rejected(*, language: Language = "en") -> Node:
    async def partner_rejected(state: Any) -> dict[str, Any]:
        decision = decision_for(state, PARTNER_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        return finish(
            "rejected",
            t("partner.rejected_summary", language, who=who, reason=reason),
            partner_candidate=None,
        )

    return partner_rejected


__all__ = [
    "PARTNER_STEP",
    "candidate_from",
    "make_create_partner",
    "make_partner_approval",
    "make_partner_rejected",
    "suggested_name",
]
