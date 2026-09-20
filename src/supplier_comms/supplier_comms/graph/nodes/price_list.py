"""A price list attached to a supplier's email becomes price updates after approval.

``load_context`` already parsed the spreadsheet and diffed it against the
supplier's prices in Odoo (``state["price_list"]``). The run pauses on a
``price_list_update`` approval that shows every row: current price, new
price, the change, and whether the code matched a product we buy. Approved,
``apply_price_list`` writes the accepted rows to ``product.supplierinfo``;
unmatched rows are never written.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from sc_core.graph import ApprovalRequest, decision_for
from sc_core.i18n import Language, t
from sc_core.schema.a2a import PriceListDiff
from sc_core.schema.autonomy import ActionFacts
from supplier_comms.graph.nodes.common import esc, finish
from supplier_comms.graph.state import Node
from supplier_comms.infra.ports import AgentPorts

PRICE_STEP = "price_list_update"


def make_price_list_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        diff = PriceListDiff.model_validate(state["price_list"])
        meta = state.get("inbound_meta") or {}
        ctx = state.get("po_context") or {}
        changed = [r for r in diff.rows if r.matched and r.current_price != r.new_price]
        biggest = max(
            (abs(r.change_pct) for r in changed if r.change_pct is not None), default=None
        )
        return ApprovalRequest(
            kind="price_list_update",
            summary=t(
                "pricelist.approval_summary",
                language,
                partner=diff.partner_name or diff.partner_id,
                n=len(changed),
                unmatched=diff.unmatched,
            )[:200],
            payload={
                "partner_id": diff.partner_id,
                "partner_name": diff.partner_name,
                "source": diff.source,
                "currency": diff.currency,
                "po_name": ctx.get("name"),
                "web_link": meta.get("web_link"),
                "graph_message_id": meta.get("graph_message_id"),
                "rows": [r.model_dump(mode="json") for r in diff.rows],
                "changed": len(changed),
                "unmatched": diff.unmatched,
                "writes": "the accepted rows on the supplier's price list in Odoo",
            },
            res_model="res.partner",
            res_id=diff.partner_id,
            po_id=ctx.get("id"),
            review_on_approval=True,
            facts=ActionFacts(
                partner_id=diff.partner_id,
                partner_name=diff.partner_name or None,
                change_pct=biggest,
            ),
        )

    return build


def make_apply_price_list(ports: AgentPorts, *, language: Language = "en") -> Node:
    async def apply_price_list(state: Any) -> dict[str, Any]:
        diff = PriceListDiff.model_validate(state["price_list"])
        decision = decision_for(state, PRICE_STEP)
        details = (decision.details or {}) if decision else {}
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        accepted = details.get("accepted_codes")
        wanted = {str(c).strip().upper() for c in accepted} if accepted is not None else None
        written = 0
        skipped: list[str] = []
        for row in diff.rows:
            if not row.matched or row.product_id is None:
                continue
            if wanted is not None and row.code.strip().upper() not in wanted:
                skipped.append(row.code)
                continue
            template = await ports.product_template_id(row.product_id)
            currency = await ports.currency_id_for(row.currency or diff.currency or "")
            if template is None or currency is None:
                skipped.append(row.code)
                continue
            await ports.upsert_price(
                partner_id=diff.partner_id,
                product_tmpl_id=template,
                product_id=row.product_id,
                price=row.new_price,
                currency_id=currency,
                min_qty=row.min_qty,
                lead_days=row.lead_days,
            )
            written += 1
        ctx = state.get("po_context") or {}
        if ctx.get("id"):
            await ports.post_note(
                int(ctx["id"]),
                t(
                    "pricelist.note",
                    language,
                    source=esc(diff.source),
                    partner=esc(diff.partner_name or str(diff.partner_id)),
                    n=written,
                    who=esc(who),
                ),
            )
        logger.bind(partner_id=diff.partner_id, written=written, skipped=len(skipped)).info(
            "price list applied"
        )
        return finish(
            "applied",
            t(
                "pricelist.applied_summary",
                language,
                n=written,
                partner=diff.partner_name or str(diff.partner_id),
            )
            + (t("pricelist.skipped", language, n=len(skipped)) if skipped else ""),
        )

    return apply_price_list


def make_price_list_rejected(*, language: Language = "en") -> Node:
    async def price_list_rejected(state: Any) -> dict[str, Any]:
        decision = decision_for(state, PRICE_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        return finish("rejected", t("pricelist.rejected_summary", language, who=who, reason=reason))

    return price_list_rejected
