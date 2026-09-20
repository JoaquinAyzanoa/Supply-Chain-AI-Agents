"""Ranking is stable and explainable: score, then price, then lead time."""

from __future__ import annotations

from datetime import date

from sc_core.schema.a2a import SupplierScore
from supplier_performance.domain.ranking import rank_suppliers
from supplier_performance.testing import price_entry


def score(partner_id: int, name: str, value: float, *, otif: float = 0.9) -> SupplierScore:
    return SupplierScore(
        partner_id=partner_id,
        partner_name=name,
        period_start=date(2025, 9, 1),
        period_end=date(2026, 9, 1),
        otif=otif,
        lead_time_mean_days=9.0,
        score=value,
        samples={"lines": 6},
    )


def test_ranking_orders_by_score_then_price_and_keeps_newcomers_visible() -> None:
    entries = [
        price_entry(45, "Proveedor Hidraulica", price=104.16, delay=10, entry_id=1),
        price_entry(45, "Proveedor Hidraulica", price=99.0, delay=10, min_qty=50, entry_id=2),
        price_entry(46, "Hidraulica Alterna", price=98.0, delay=7, entry_id=3),
        price_entry(47, "Nuevo Proveedor", price=90.0, delay=5, entry_id=4),
    ]
    scores = {
        45: score(45, "Proveedor Hidraulica", 88.5),
        46: score(46, "Hidraulica Alterna", 88.5),
    }
    ranking = rank_suppliers(49, entries, scores)
    assert ranking.product_id == 49
    assert [(r.rank, r.partner_id) for r in ranking.suppliers] == [(1, 46), (2, 45), (3, 47)]
    first, second, new = ranking.suppliers
    assert first.price == 98.0 and second.price == 104.16  # the lowest minimum quantity's price
    assert new.score is None and new.why.startswith("no history yet") and "90 USD" in new.why
    assert "score 88.5" in second.why and "OTIF 90%" in second.why and "9 d observed" in second.why
    assert rank_suppliers(49, entries, scores).suppliers == ranking.suppliers  # stable
    assert rank_suppliers(49, [], scores).suppliers == []
