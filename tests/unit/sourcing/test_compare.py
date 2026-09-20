"""The comparison in code: landed cost, lead time, score, ties and gaps."""

from __future__ import annotations

import pytest

from sc_core.schema.a2a import QuoteLine
from sourcing.compare import Offer, Weights, compare, landed
from sourcing.models import BasketLine

VALVE = BasketLine(product_id=1, product="[CBEA-LHN] Válvula", qty=10, last_paid=100.0)
HOSE = BasketLine(product_id=2, product="[MANG-12] Manguera", qty=5, last_paid=20.0)


def offer(
    pid: int,
    name: str,
    prices: dict[int, float | None],
    *,
    lead: int | None = None,
    score: float | None = None,
    source: str = "reply",
    first: bool = False,
) -> Offer:
    return Offer(
        partner_id=pid,
        partner_name=name,
        lines=[QuoteLine(product_id=k, product="", qty=1, price_unit=v) for k, v in prices.items()],
        source=source,  # type: ignore[arg-type]
        lead_days=lead,
        score=score,
        first_time=first,
    )


def test_landed_adds_the_freight_estimate() -> None:
    assert landed(100.0, 5.0) == 105.0
    assert landed(90.72, 0.0) == 90.72


@pytest.mark.parametrize(
    ("offers", "expected_winner", "expected_reason"),
    [
        # the cheapest landed total wins when lead time and score are equal
        (
            [
                offer(8, "A", {1: 104.16}, lead=30, score=80),
                offer(9, "B", {1: 114.24}, lead=30, score=80),
            ],
            8,
            "lowest landed total",
        ),
        # a cheaper but much slower, unscored supplier loses to a proven one
        (
            [
                offer(8, "A", {1: 104.16}, lead=30, score=82),
                offer(10, "C", {1: 90.72}, lead=60, source="price_list"),
            ],
            8,
            "score 82/100",
        ),
        # a far cheaper offer wins even with a worse lead time
        (
            [
                offer(8, "A", {1: 104.16}, lead=30, score=82),
                offer(10, "C", {1: 60.0}, lead=60, score=70),
            ],
            10,
            "lowest landed total",
        ),
        # a tie on price: the faster one wins
        (
            [
                offer(8, "A", {1: 100.0}, lead=30, score=80),
                offer(9, "B", {1: 100.0}, lead=18, score=80),
            ],
            9,
            "fastest: 18 day(s)",
        ),
    ],
)
def test_recommendation_table(
    offers: list[Offer], expected_winner: int, expected_reason: str
) -> None:
    result = compare(
        round_id=1, basket=[VALVE], offers=offers, freight_pct=5.0, invited=len(offers)
    )
    assert result.recommended_partner_id == expected_winner
    best = next(q for q in result.quotes if q.recommended)
    assert best.rank == 1 and expected_reason in best.reasons
    assert result.recommendation.startswith(best.partner_name)


def test_a_partial_basket_is_penalised_and_named() -> None:
    offers = [
        offer(8, "A", {1: 104.16, 2: 21.0}, lead=30, score=80),
        offer(9, "B", {1: 95.0}, lead=30, score=80),  # cheaper on the valve, silent on the hose
    ]
    result = compare(round_id=2, basket=[VALVE, HOSE], offers=offers, freight_pct=0.0)
    assert result.recommended_partner_id == 8
    partial = next(q for q in result.quotes if q.partner_id == 9)
    assert not partial.complete and "not quoted: [MANG-12] Manguera" in partial.reasons
    assert partial.total == 950.0  # only what was quoted
    full = next(q for q in result.quotes if q.partner_id == 8)
    assert full.total == 1041.6 + 105.0 and full.complete
    assert result.last_paid == {"1": 100.0, "2": 20.0}


def test_nobody_priced_means_no_recommendation() -> None:
    result = compare(
        round_id=3,
        basket=[VALVE],
        offers=[offer(8, "A", {1: None}, source="none")],
        freight_pct=5.0,
    )
    assert result.recommended_partner_id is None and result.recommendation == ""
    assert result.quotes[0].total is None and result.quotes[0].source == "none"


def test_weights_change_the_outcome() -> None:
    offers = [
        offer(8, "A", {1: 100.0}, lead=30, score=90),
        offer(9, "B", {1: 92.0}, lead=30, score=40),
    ]
    price_first = compare(
        round_id=4,
        basket=[VALVE],
        offers=offers,
        weights=Weights(price=0.9, lead_time=0.05, score=0.05),
    )
    score_first = compare(
        round_id=4,
        basket=[VALVE],
        offers=offers,
        weights=Weights(price=0.2, lead_time=0.1, score=0.7),
    )
    assert price_first.recommended_partner_id == 9
    assert score_first.recommended_partner_id == 8
    assert score_first.weights["score"] == 0.7


def test_first_time_and_list_price_are_said_in_words() -> None:
    result = compare(
        round_id=5,
        basket=[VALVE],
        offers=[offer(10, "C", {1: 90.72}, lead=60, source="price_list", first=True)],
        freight_pct=5.0,
    )
    [quote] = result.quotes
    assert (
        "list price, no reply yet" in quote.reasons
        and "first order with this supplier" in quote.reasons
    )
    assert quote.lines[0].landed_unit == 95.256 and quote.total == 952.56


def test_the_default_award_per_line_follows_the_recommendation_not_the_cheapest() -> None:
    """Approving without choosing must give the line to the recommended supplier; the
    cheapest, slow and unreliable one is named so the buyer can still pick it."""
    result = compare(
        round_id=1,
        basket=[VALVE],
        offers=[
            offer(8, "Proven", {1: 104.16}, lead=25, score=75),
            offer(10, "Cheap and slow", {1: 85.00}, lead=55, score=45),
        ],
        weights=Weights(),
        freight_pct=5.0,
    )
    assert result.recommended_partner_id == 8
    [award] = result.line_awards
    assert award.partner_id == 8
    assert award.reasons[0].startswith("best on price, lead time and score")
    assert award.reasons[1] == "cheapest is Cheap and slow at 89.25"


def test_the_reasons_follow_the_language_the_buyer_reads() -> None:
    result = compare(
        round_id=1,
        basket=[VALVE],
        offers=[
            offer(8, "Proven", {1: 104.16}, lead=25, score=75),
            offer(10, "Cheap and slow", {1: 85.00}, lead=55, score=45),
        ],
        weights=Weights(),
        freight_pct=5.0,
        language="es",
    )
    proven, cheap = result.quotes
    assert proven.reasons == [
        "22.5% sobre el más bajo",
        "el más rápido: 25 día(s)",
        "puntaje 75/100",
    ]
    assert cheap.reasons[0] == "el menor total puesto en almacén"
    [award] = result.line_awards
    assert award.reasons[1] == "el más barato es Cheap and slow a 89.25"
    assert ". Le sigue: Cheap and slow (" in result.recommendation
