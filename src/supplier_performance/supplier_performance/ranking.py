"""Which supplier for a product: the latest scores joined with the price list.

Order: score first, then price, then the shorter lead time. Suppliers on the
price list without a score are listed last with their price and lead time,
so a new supplier is visible, never hidden.
"""

from __future__ import annotations

from sc_core.odoo.models import SupplierInfo
from sc_core.schema.a2a import RankedSupplier, SupplierRanking, SupplierScore


def rank_suppliers(
    product_id: int, entries: list[SupplierInfo], scores: dict[int, SupplierScore]
) -> SupplierRanking:
    by_partner: dict[int, SupplierInfo] = {}
    for entry in entries:  # the price list may list a supplier more than once (min quantities)
        current = by_partner.get(entry.partner_id.id)
        if current is None or entry.min_qty < current.min_qty:
            by_partner[entry.partner_id.id] = entry
    ranked: list[RankedSupplier] = []
    for partner_id, entry in by_partner.items():
        score = scores.get(partner_id)
        ranked.append(
            RankedSupplier(
                partner_id=partner_id,
                partner_name=entry.partner_id.name,
                score=score.score if score else None,
                otif=score.otif if score else None,
                lead_time_mean_days=score.lead_time_mean_days if score else None,
                price=entry.price,
                currency=entry.currency_id.name if entry.currency_id else None,
                min_qty=entry.min_qty,
                promised_lead_days=entry.delay,
                samples=score.samples if score else {},
                why=_why(entry, score),
            )
        )
    ranked.sort(
        key=lambda r: (
            -(r.score if r.score is not None else -1.0),
            r.price if r.price is not None else float("inf"),
            r.lead_time_mean_days if r.lead_time_mean_days is not None else r.promised_lead_days,
        )
    )
    numbered = [
        item.model_copy(update={"rank": position}) for position, item in enumerate(ranked, start=1)
    ]
    return SupplierRanking(product_id=product_id, suppliers=numbered)


def _why(entry: SupplierInfo, score: SupplierScore | None) -> str:
    parts = []
    if score is None:
        parts.append("no history yet")
    else:
        parts.append(f"score {score.score:g}")
        if score.otif is not None:
            parts.append(f"OTIF {100 * score.otif:.0f}%")
        if score.lead_time_mean_days is not None:
            parts.append(f"lead time {score.lead_time_mean_days:g} d observed")
    parts.append(
        f"price {entry.price:g} {entry.currency_id.name if entry.currency_id else ''}".strip()
    )
    if entry.delay:
        parts.append(f"{entry.delay} d promised")
    return ", ".join(parts)
