"""Order consolidation: reach a supplier's free-freight threshold by pulling forward
the products we would buy from them soon anyway, when the stock it costs is less
than the freight it saves.

Per supplier with something to order this run: if the order value is below
the supplier's free-freight threshold and freight has a cost, candidates are
that supplier's other products whose next order is not yet due, nearest
first (fewest days of cover). Each candidate adds its next order quantity
(order-up-to minus position) at the reference price until the threshold is
reached. Stock cost = value pulled forward x holding rate x days early / 365.
The proposal stands only when the freight saved exceeds the stock cost; the
person ticks the lines like any other.
"""

from __future__ import annotations

from math import ceil

from pydantic import Field

from sc_core.schema.base import StrictModel
from sc_core.schema.planning import ConsolidationNote, ReplenishmentLine


class FreightTerms(StrictModel):
    """What the supplier profile says about freight (facts ``free_freight_over``,
    ``freight_cost``); zero means unknown."""

    partner_id: int
    partner_name: str = ""
    free_freight_over: float = Field(default=0.0, ge=0)
    freight_cost: float = Field(default=0.0, ge=0)


def consolidate(
    lines: list[ReplenishmentLine],
    terms: dict[int, FreightTerms],
    *,
    holding_pct_year: float,
) -> list[ReplenishmentLine]:
    """The lines with the consolidation proposals added (as new ``consolidate`` lines)."""
    by_supplier: dict[int, list[ReplenishmentLine]] = {}
    for line in lines:
        if line.supplier_id is not None:
            by_supplier.setdefault(line.supplier_id, []).append(line)
    extra: list[ReplenishmentLine] = []
    for supplier_id, group in by_supplier.items():
        term = terms.get(supplier_id)
        if term is None or term.free_freight_over <= 0 or term.freight_cost <= 0:
            continue
        ordering = [ln for ln in group if ln.order_qty > 0 and ln.action != "consolidate"]
        if not ordering:
            continue  # nothing goes to this supplier this run: nothing to top up
        value = sum(ln.order_value for ln in ordering)
        if value >= term.free_freight_over:
            continue
        gap = term.free_freight_over - value
        candidates = sorted(
            (
                ln
                for ln in group
                if ln.order_qty <= 0
                and ln.unit_price
                and ln.coverage_days is not None
                and ln.order_up_to > ln.position
                and ln.held_until is None
            ),
            key=lambda ln: (ln.coverage_days or 0.0, ln.product_ref),
        )
        pulled: list[ReplenishmentLine] = []
        stock_cost = 0.0
        for candidate in candidates:
            if gap <= 0:
                break
            qty = float(ceil(candidate.order_up_to - candidate.position - 1e-9))
            if qty <= 0 or not candidate.unit_price:
                continue
            days_early = max(0.0, (candidate.coverage_days or 0.0) - candidate.lead_time_days)
            line_value = qty * candidate.unit_price
            cost = line_value * (holding_pct_year / 100.0) * (days_early / 365.0)
            stock_cost += cost
            gap -= line_value
            pulled.append(
                candidate.model_copy(
                    update={
                        "order_qty": qty,
                        "action": "consolidate",
                        "consolidation": ConsolidationNote(
                            supplier_id=supplier_id,
                            supplier_name=term.partner_name or candidate.supplier_name or "",
                            freight_saved=round(term.freight_cost, 2),
                            stock_cost=round(cost, 2),
                            days_early=round(days_early, 1),
                            threshold=term.free_freight_over,
                        ),
                        "explanation": (
                            f"Pulled forward {qty:g} units to reach "
                            f"{term.partner_name or 'the supplier'}'s "
                            f"free-freight threshold ({term.free_freight_over:,.0f}); "
                            f"{days_early:.0f} day(s) early, stock cost {cost:,.2f} against "
                            f"freight {term.freight_cost:,.2f}."
                        ),
                    }
                )
            )
        if pulled and gap <= 0 and stock_cost < term.freight_cost:
            extra.extend(pulled)
    if not extra:
        return list(lines)
    replaced = {ln.line_id: ln for ln in extra}
    return [replaced.get(ln.line_id, ln) for ln in lines]


__all__ = ["FreightTerms", "consolidate"]
