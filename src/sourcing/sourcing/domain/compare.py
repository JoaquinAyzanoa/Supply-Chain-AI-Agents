"""Compare the offers of a round, in code.

An offer is a supplier's price per basket line (from the reply written on
the RFQ, or from the price list when they did not answer), its lead time and
its score. The landed unit cost adds the freight estimate; the composite
weighs the landed total (lower is better), the longest lead time and the
supplier score, each normalised against the best offer, and takes a penalty
when the basket is not fully quoted. The recommendation is the best composite;
the reasons say why in words a buyer can check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sc_core.schema.a2a import ComparedQuote, LineAward, QuoteComparison, QuoteLine
from sourcing.domain.models import BasketLine


@dataclass(frozen=True)
class Weights:
    price: float = 0.6
    lead_time: float = 0.2
    score: float = 0.2
    incomplete_penalty: float = 0.25

    def as_dict(self) -> dict[str, float]:
        return {
            "price": self.price,
            "lead_time": self.lead_time,
            "score": self.score,
            "incomplete_penalty": self.incomplete_penalty,
        }


@dataclass
class Offer:
    partner_id: int
    partner_name: str
    lines: list[QuoteLine]  # price_unit None: not quoted
    source: Literal["reply", "price_list", "none"] = "none"
    po_name: str | None = None
    currency: str | None = None
    lead_days: int | None = None
    score: float | None = None
    first_time: bool = False


def landed(price: float, freight_pct: float) -> float:
    return round(price * (1.0 + freight_pct / 100.0), 4)


def compare(
    *,
    round_id: int,
    basket: list[BasketLine],
    offers: list[Offer],
    freight_pct: float = 0.0,
    weights: Weights | None = None,
    invited: int = 0,
    source_po_name: str | None = None,
    language: str = "en",
) -> QuoteComparison:
    weights = weights or Weights()
    say = _Words(language)
    wanted = {line.product_id: line for line in basket}
    quotes: list[ComparedQuote] = []
    for offer in offers:
        lines: list[QuoteLine] = []
        total = 0.0
        quoted_products: set[int] = set()
        for line in offer.lines:
            want = wanted.get(line.product_id)
            if want is None:
                continue
            unit = landed(line.price_unit, freight_pct) if line.price_unit is not None else None
            lines.append(
                line.model_copy(
                    update={"qty": want.qty, "product": want.product, "landed_unit": unit}
                )
            )
            if unit is not None:
                total += unit * want.qty
                quoted_products.add(line.product_id)
        complete = quoted_products == set(wanted)
        quotes.append(
            ComparedQuote(
                partner_id=offer.partner_id,
                partner_name=offer.partner_name,
                po_name=offer.po_name,
                source=offer.source if quoted_products else "none",
                currency=offer.currency,
                lines=lines,
                total=round(total, 2) if quoted_products else None,
                lead_days=offer.lead_days,
                score=offer.score,
                complete=complete,
                first_time_supplier=offer.first_time,
            )
        )
    priced = [q for q in quotes if q.total is not None]
    if priced:
        best_total = min(q.total for q in priced if q.total is not None)
        leads = [q.lead_days for q in priced if q.lead_days is not None]
        best_lead = min(leads) if leads else None
        scored: list[ComparedQuote] = []
        for quote in quotes:
            if quote.total is None:
                scored.append(quote)
                continue
            price_norm = best_total / quote.total if quote.total > 0 else 1.0
            if best_lead is not None and quote.lead_days:
                lead_norm = best_lead / quote.lead_days
            elif best_lead is not None and quote.lead_days == 0:
                lead_norm = 1.0
            else:
                lead_norm = 0.5
            score_norm = (quote.score / 100.0) if quote.score is not None else 0.5
            composite = (
                weights.price * price_norm
                + weights.lead_time * lead_norm
                + weights.score * score_norm
                - (0.0 if quote.complete else weights.incomplete_penalty)
            )
            scored.append(
                quote.model_copy(
                    update={
                        "composite": round(composite, 4),
                        "reasons": _reasons(quote, best_total, best_lead, wanted, say),
                    }
                )
            )
        quotes = scored
    ranked = sorted(
        quotes,
        key=lambda q: (
            -(q.composite if q.composite is not None else -1.0),
            q.total if q.total is not None else float("inf"),
        ),
    )
    recommended_id = next((q.partner_id for q in ranked if q.composite is not None), None)
    ranked = [
        quote.model_copy(
            update={"rank": position, "recommended": quote.partner_id == recommended_id}
        )
        for position, quote in enumerate(ranked, start=1)
    ]
    recommended = next((q for q in ranked if q.recommended), None)
    line_awards = _line_awards(basket, ranked, say)
    return QuoteComparison(
        round_id=round_id,
        source_po_name=source_po_name,
        basket=[
            QuoteLine(product_id=line.product_id, product=line.product, qty=line.qty)
            for line in basket
        ],
        quotes=ranked,
        recommended_partner_id=recommended.partner_id if recommended else None,
        line_awards=line_awards,
        recommendation=_recommendation(recommended, ranked, say) if recommended else "",
        freight_pct=freight_pct,
        weights=weights.as_dict(),
        last_paid={
            str(line.product_id): line.last_paid for line in basket if line.last_paid is not None
        },
        invited=invited,
        replied=sum(1 for q in quotes if q.source == "reply"),
    )


def _line_awards(
    basket: list[BasketLine], ranked: list[ComparedQuote], say: _Words
) -> list[LineAward]:
    """Per product: the quote that ranks best on the same weights as the recommendation
    (price, lead time, score), so approving without choosing follows what was recommended.
    The cheapest is named when it is someone else; the person may still award otherwise."""
    awards: list[LineAward] = []
    for want in basket:
        candidates: list[tuple[float, float, ComparedQuote, QuoteLine]] = []
        for quote in ranked:
            for line in quote.lines:
                if line.product_id == want.product_id and line.landed_unit is not None:
                    candidates.append((-(quote.composite or 0.0), line.landed_unit, quote, line))
        if not candidates:
            continue
        candidates.sort(key=lambda c: (c[0], c[1]))
        _, _, best, line = candidates[0]
        cheapest = min(candidates, key=lambda c: c[1])
        if cheapest[2].partner_id == best.partner_id:
            reasons = [say("cheapest_unit", price=line.landed_unit)]
            if len(candidates) > 1:
                runner = sorted(candidates, key=lambda c: c[1])[1]
                reasons.append(
                    say("next_at", name=runner[2].partner_name, price=runner[3].landed_unit)
                )
        else:
            reasons = [
                say("best_overall", price=line.landed_unit),
                say("cheapest_is", name=cheapest[2].partner_name, price=cheapest[3].landed_unit),
            ]
        if best.lead_days is not None:
            reasons.append(say("days", days=best.lead_days))
        awards.append(
            LineAward(
                product_id=want.product_id,
                product=want.product,
                partner_id=best.partner_id,
                partner_name=best.partner_name,
                po_name=best.po_name,
                landed_unit=line.landed_unit,
                reasons=reasons,
            )
        )
    return awards


def _reasons(
    quote: ComparedQuote,
    best_total: float,
    best_lead: int | None,
    wanted: dict[int, BasketLine],
    say: _Words,
) -> list[str]:
    reasons: list[str] = []
    assert quote.total is not None
    if quote.total <= best_total + 1e-9:
        reasons.append(say("lowest_total"))
    elif best_total > 0:
        reasons.append(say("above_lowest", pct=100 * (quote.total / best_total - 1)))
    if quote.lead_days is not None:
        if best_lead is not None and quote.lead_days <= best_lead:
            reasons.append(say("fastest", days=quote.lead_days))
        else:
            reasons.append(say("lead_time", days=quote.lead_days))
    if quote.score is not None:
        reasons.append(say("score", score=quote.score))
    else:
        reasons.append(say("no_score"))
    if not quote.complete:
        missing = [
            wanted[pid].product
            for pid in wanted
            if pid not in {line.product_id for line in quote.lines if line.landed_unit is not None}
        ]
        reasons.append(say("not_quoted", products=", ".join(missing)))
    if quote.source == "price_list":
        reasons.append(say("list_price"))
    if quote.first_time_supplier:
        reasons.append(say("first_order"))
    return reasons


def _recommendation(best: ComparedQuote, ranked: list[ComparedQuote], say: _Words) -> str:
    others = [q for q in ranked if q is not best and q.total is not None]
    text = f"{best.partner_name}: " + "; ".join(best.reasons)
    if others:
        runner = others[0]
        text += say("next", name=runner.partner_name, reasons="; ".join(runner.reasons))
    unpriced = [q.partner_name for q in ranked if q.total is None]
    if unpriced:
        text += say("no_price", names=", ".join(unpriced))
    return text


# The reasons are read by the buyer in the award, so they follow the instance's language.
WORDS: dict[str, dict[str, str]] = {
    "en": {
        "lowest_total": "lowest landed total",
        "above_lowest": "{pct:.1f}% above the lowest",
        "fastest": "fastest: {days} day(s)",
        "lead_time": "{days} day(s) lead time",
        "score": "score {score:.0f}/100",
        "no_score": "no score yet",
        "not_quoted": "not quoted: {products}",
        "list_price": "list price, no reply yet",
        "first_order": "first order with this supplier",
        "cheapest_unit": "cheapest landed unit {price:.2f}",
        "next_at": "next {name} at {price:.2f}",
        "best_overall": "best on price, lead time and score at {price:.2f}",
        "cheapest_is": "cheapest is {name} at {price:.2f}",
        "days": "{days} day(s)",
        "next": ". Next: {name} ({reasons})",
        "no_price": ". No price from: {names}",
    },
    "es": {
        "lowest_total": "el menor total puesto en almacén",
        "above_lowest": "{pct:.1f}% sobre el más bajo",
        "fastest": "el más rápido: {days} día(s)",
        "lead_time": "{days} día(s) de plazo",
        "score": "puntaje {score:.0f}/100",
        "no_score": "aún sin puntaje",
        "not_quoted": "no cotizó: {products}",
        "list_price": "precio de lista, aún sin respuesta",
        "first_order": "primera orden con este proveedor",
        "cheapest_unit": "el menor costo unitario puesto en almacén: {price:.2f}",
        "next_at": "le sigue {name} a {price:.2f}",
        "best_overall": "el mejor en precio, plazo y puntaje, a {price:.2f}",
        "cheapest_is": "el más barato es {name} a {price:.2f}",
        "days": "{days} día(s)",
        "next": ". Le sigue: {name} ({reasons})",
        "no_price": ". Sin precio de: {names}",
    },
}


class _Words:
    """The comparison's phrases in one language (English when the language is unknown)."""

    def __init__(self, language: str) -> None:
        self._words = WORDS.get(language, WORDS["en"])

    def __call__(self, key: str, **values: object) -> str:
        return self._words[key].format(**values)


__all__ = ["Offer", "Weights", "compare", "landed"]
