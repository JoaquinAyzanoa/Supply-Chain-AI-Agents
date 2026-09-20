"""Alternative source for a late order: who else lists these products.

Alternates with a valid list price for the whole basket are compared at once
and proposed as a direct order (``award`` approval, mode ``direct``); when
nobody lists the products but suppliers exist, a quote round is started;
when there is nobody, a person is told.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from sc_core.graph import ApprovalGateway, ApprovalRequest
from sc_core.i18n import Language, t
from sc_core.schema.a2a import QuoteLine
from sourcing.compare import Offer, Weights, compare
from sourcing.nodes.common import LimitsReader, basket_of, fail, finish, order_of, task_of
from sourcing.nodes.negotiate import ESCALATION_STEP
from sourcing.ports import SourcingPorts
from sourcing.state import Node


def make_find_alternates(
    ports: SourcingPorts,
    limits: LimitsReader,
    approvals: ApprovalGateway,
    *,
    weights: Weights,
    now: Callable[[], datetime],
    language: Language = "en",
) -> Node:
    async def find_alternates(state: Any) -> dict[str, Any]:
        task = task_of(state)
        order = order_of(state)
        basket = basket_of(state)
        if order is None or not basket:
            return fail("alternate_source needs an order with lines")
        current = await limits()
        excluded = {order.partner_id, *task.exclude_partner_ids}
        product_ids = [b.product_id for b in basket]
        options = [o for o in await ports.options_for(product_ids) if o.partner_id not in excluded]
        entries = [
            e for e in await ports.price_entries(product_ids) if e.partner_id not in excluded
        ]
        offers: list[Offer] = []
        for option in options:
            listed = [e for e in entries if e.partner_id == option.partner_id]
            if {e.product_id for e in listed} != set(product_ids):
                continue  # a direct order needs a price for every line
            offers.append(
                Offer(
                    partner_id=option.partner_id,
                    partner_name=option.partner_name,
                    source="price_list",
                    currency=listed[0].currency,
                    lines=[
                        QuoteLine(
                            product_id=e.product_id,
                            product=next(b.product for b in basket if b.product_id == e.product_id),
                            qty=1.0,
                            price_unit=e.price,
                            lead_days=e.lead_days,
                            min_qty=e.min_qty,
                        )
                        for e in listed
                    ],
                    lead_days=max((e.lead_days or 0 for e in listed), default=None),
                    score=option.score,
                    first_time=option.first_time,
                )
            )
        if offers:
            round_ = await ports.create_round(
                case_id=state["case_id"],
                basket=basket,
                deadline=now() + timedelta(days=current.deadline_days),
                source_po_name=order.po_name,
                incumbent_partner_id=order.partner_id,
                created_by=task.reason or "alternate_source",
            )
            comparison = compare(
                round_id=round_.id,
                basket=basket,
                offers=offers,
                freight_pct=current.freight_pct,
                weights=weights,
                invited=0,
                source_po_name=order.po_name,
                language=language,
            )
            updated = await ports.update_round(
                round_.id, status="comparing", comparison=comparison.model_dump(mode="json")
            )
            logger.bind(po_name=order.po_name, alternates=len(offers)).info("alternates found")
            return {
                "round": updated.model_dump(mode="json"),
                "comparison": comparison.model_dump(mode="json"),
                "mode": "direct",
                "options": [o.model_dump(mode="json") for o in options],
            }
        if options:
            # nobody lists every product: ask them, the award comes with the quotes
            return {"options": [o.model_dump(mode="json") for o in options], "mode": "invite"}
        summary = t("sourcing.no_alternate", language, po=order.po_name)
        update = await approvals.prepare(
            state,
            ESCALATION_STEP,
            ApprovalRequest(
                kind="escalation",
                summary=summary[:200],
                payload={"po_name": order.po_name, "products": [b.product for b in basket]},
                po_id=order.po_id,
            ),
        )
        pending = (update.get("pending_approvals") or [{}])[0]
        return {
            **update,
            **finish("escalated", summary, escalation_approval_id=pending.get("approval_id")),
        }

    return find_alternates


__all__ = ["make_find_alternates"]
