"""The generated plan is deterministic, follows the profiles and never runs stock negative."""

from __future__ import annotations

from datetime import date, timedelta

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
        if product.demand.since_months is not None:
            # launched recently: nothing before the launch, something after it
            launch = plan.end - timedelta(days=int(product.demand.since_months * 30.44))
            assert not [d for d in plan.deliveries if d.code == product.code and d.day < launch]
            assert total > 0
            continue
        expected = product.demand.mean_weekly * weeks * (1 + product.demand.trend_per_year / 2)
        assert abs(total - expected) / expected < 0.25, (product.code, total, expected)
    by_month = plan.demand_by_month("CBEA-LHN")
    peaks = [q for m, q in by_month.items() if m.endswith(("-04", "-05", "-10", "-11"))]
    troughs = [q for m, q in by_month.items() if m.endswith(("-12", "-01"))]
    assert sum(peaks) / len(peaks) > sum(troughs) / len(troughs)

    # intermittent products sell in a minority of the weeks, at the same weekly mean
    intermittent = next(p for p in ds.products if p.demand.pattern == "intermittent")
    weekly = {}
    for d in plan.deliveries:
        if d.code == intermittent.code:
            key = d.day - timedelta(days=d.day.weekday())
            weekly[key] = weekly.get(key, 0) + d.qty
    assert len(weekly) < 0.75 * weeks


def test_supply_keeps_stock_non_negative_and_mixes_outcomes() -> None:
    ds = load(DEFAULT_PATH)
    plan = build_plan(ds, today=TODAY)
    for product in ds.products:
        levels = simulate_on_hand(plan, product.code)
        assert levels and min(level for _, level in levels) >= 0, product.code
    outcomes = {p.outcome for p in plan.purchases}
    assert outcomes == {"on_time", "late", "partial"}
    suppliers = {p.supplier for p in plan.purchases}
    assert suppliers == set(ds.suppliers)  # every supplier has receipts to be scored on
    assert all(p.received >= p.ordered for p in plan.purchases)
    assert any(d.backorder for d in plan.deliveries)
    # the importer is late far more often than the alternate supplier
    late_share = {
        key: sum(1 for p in plan.purchases if p.supplier == key and p.outcome == "late")
        / max(sum(1 for p in plan.purchases if p.supplier == key), 1)
        for key in ds.suppliers
    }
    assert late_share["importer"] > late_share["alternate"]
