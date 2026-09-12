"""The generated plan is deterministic, follows the profiles and never runs stock negative."""

from __future__ import annotations

from datetime import date

from odoo_seed_lib.dataset import DEFAULT_PATH, load
from odoo_seed_lib.plan import build_plan, simulate_on_hand

TODAY = date(2026, 9, 12)


def test_plan_is_deterministic() -> None:
    ds = load(DEFAULT_PATH)
    a, b = build_plan(ds, today=TODAY), build_plan(ds, today=TODAY)
    assert a.deliveries == b.deliveries and a.purchases == b.purchases and a.opening == b.opening
    other = build_plan(ds.model_copy(update={"seed": 1}), today=TODAY)
    assert other.deliveries != a.deliveries


def test_demand_matches_profiles_and_seasonality() -> None:
    ds = load(DEFAULT_PATH)
    plan = build_plan(ds, today=TODAY)
    weeks = (plan.end - plan.start).days / 7
    for product in ds.products:
        total = sum(d.qty for d in plan.deliveries if d.code == product.code)
        expected = product.demand.mean_weekly * weeks * (1 + product.demand.trend_per_year / 2)
        assert abs(total - expected) / expected < 0.25, (product.code, total, expected)
    by_month = plan.demand_by_month("CBEA-LHN")
    peaks = [q for m, q in by_month.items() if m.endswith(("-04", "-05", "-10", "-11"))]
    troughs = [q for m, q in by_month.items() if m.endswith(("-12", "-01"))]
    assert sum(peaks) / len(peaks) > sum(troughs) / len(troughs)


def test_supply_keeps_stock_non_negative_and_mixes_outcomes() -> None:
    ds = load(DEFAULT_PATH)
    plan = build_plan(ds, today=TODAY)
    for product in ds.products:
        levels = simulate_on_hand(plan, product.code)
        assert levels and min(level for _, level in levels) >= 0, product.code
    outcomes = {p.outcome for p in plan.purchases}
    assert outcomes == {"on_time", "late", "partial"}
    suppliers = {p.supplier for p in plan.purchases}
    assert suppliers == {"primary", "alternate"}
    assert all(p.received >= p.ordered for p in plan.purchases)
    assert any(d.backorder for d in plan.deliveries)
