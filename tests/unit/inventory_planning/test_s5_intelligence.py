"""S5: seasonal and intermittent forecasts, the demand calendar, the risk radar and
order consolidation, all on synthetic data."""

from __future__ import annotations

import math
from datetime import date, timedelta

from inventory_planning.consolidation import FreightTerms, consolidate
from inventory_planning.forecasting import (
    detect_season,
    holt_winters,
    select_forecast,
    tsb,
)
from inventory_planning.forecasting.calendar import adjust_history, forecast_uplift
from inventory_planning.models import PlanningDataset, ProductData
from inventory_planning.nodes.forecast import forecast_product
from inventory_planning.risk import product_risk, risk_report, supplier_risks
from sc_core.odoo.models import DailyDemand, IncomingLine, SupplierTerms
from sc_core.schema.calendar import CalendarEvent
from sc_core.schema.planning import ReplenishmentLine
from sc_core.shared.stats import stockout_probability

AS_OF = date(2026, 9, 14)


def seasonal_series(periods: int = 104, season: int = 13, level: float = 50.0) -> list[float]:
    """Two years of weekly demand with a quarterly cycle and a mild trend."""
    return [
        round(level + 0.1 * i + 20.0 * math.sin(2 * math.pi * i / season), 2)
        for i in range(periods)
    ]


def intermittent_series(periods: int = 60) -> list[float]:
    return [float(6 if i % 4 == 0 else 0) for i in range(periods)]


# --- forecasting -------------------------------------------------------------------------


def test_the_season_is_detected_by_autocorrelation() -> None:
    assert detect_season(seasonal_series()) == 13
    assert detect_season([5.0] * 40) is None  # flat: no season
    assert detect_season([1.0, 2.0, 3.0]) is None  # too short


def test_holt_winters_beats_the_level_methods_on_a_seasonal_series() -> None:
    result = select_forecast(seasonal_series())
    assert result.method in ("holt_winters_add", "holt_winters_mul"), result.method
    assert result.season_length == 13
    assert result.wape is not None and result.wape < 0.2
    # the two next periods follow the cycle, not a flat mean
    forecast = holt_winters(seasonal_series(), 4, seasonal="add")
    assert forecast.params["season_length"] == 13.0 and len(forecast.values) == 4


def test_holt_winters_without_a_season_is_the_moving_average() -> None:
    forecast = holt_winters([10.0, 12.0, 11.0, 10.0, 12.0, 11.0, 10.0, 12.0, 11.0, 10.0], 3)
    assert forecast.method == "holt_winters_add" and forecast.params["season_length"] == 0.0
    assert forecast.values == [forecast.values[0]] * 3


def test_tsb_decays_when_demand_stops_and_croston_does_not() -> None:
    dead = intermittent_series(40) + [0.0] * 30
    tsb_rate = tsb(dead, 1).values[0]
    from inventory_planning.forecasting import croston

    croston_rate = croston(dead, 1).values[0]
    assert tsb_rate < 0.3 and croston_rate > 1.0
    result = select_forecast(intermittent_series())
    assert result.intermittent and result.method in ("croston", "tsb")


# --- the demand calendar ---------------------------------------------------------------


def test_history_is_normalised_for_past_promotions_and_projects() -> None:
    start = date(2026, 6, 1)  # a Monday
    promo = CalendarEvent(
        kind="promotion",
        name="Feria",
        start_date=date(2026, 6, 8),
        end_date=date(2026, 6, 14),
        factor=2.0,
    )
    project = CalendarEvent(
        kind="project",
        name="Planta norte",
        product_id=1,
        start_date=date(2026, 6, 15),
        end_date=date(2026, 6, 21),
        quantity=70,
    )
    buckets = [10.0, 20.0, 80.0, 10.0]
    assert adjust_history(buckets, start, [promo, project]) == [10.0, 10.0, 10.0, 10.0]
    # a promotion covering half a week only removes half the uplift
    half = CalendarEvent(
        kind="promotion",
        name="x",
        start_date=date(2026, 6, 4),
        end_date=date(2026, 6, 7),
        factor=2.0,
    )
    assert adjust_history([15.0], start, [half]) == [15.0 / (1 + 4 / 7)]


def test_the_forecast_rises_for_the_events_ahead() -> None:
    holiday = CalendarEvent(
        kind="holiday",
        name="Fiestas",
        start_date=AS_OF + timedelta(days=10),
        end_date=AS_OF + timedelta(days=19),
        factor=0.5,
    )
    project = CalendarEvent(
        kind="project",
        name="Obra",
        product_id=1,
        start_date=AS_OF + timedelta(days=5),
        end_date=AS_OF + timedelta(days=64),
        quantity=120,
    )
    # 10 days at half demand over 60 days: -10 * 0.5 * 2 / 60 per day
    assert forecast_uplift(2.0, AS_OF, 60, [holiday]) == -(10 * 2.0 * 0.5) / 60
    # a 60-day project of which 55 days fall in the horizon
    assert forecast_uplift(2.0, AS_OF, 60, [project]) == (120 * 55 / 60) / 60
    assert forecast_uplift(2.0, AS_OF, 60, []) == 0.0


def _product(
    product_id: int = 1,
    *,
    daily: float = 2.0,
    on_hand: float = 10.0,
    incoming: list[IncomingLine] | None = None,
) -> ProductData:
    start = AS_OF - timedelta(days=120)
    demand = [
        DailyDemand(
            product_id=product_id, day=start + timedelta(days=i), ordered=daily, delivered=daily
        )
        for i in range(120)
    ]
    return ProductData(
        product_id=product_id,
        ref=f"P{product_id}",
        name=f"Product {product_id}",
        category="Hydraulics",
        standard_price=100.0,
        demand=demand,
        on_hand=on_hand,
        incoming=incoming or [],
        suppliers=[
            SupplierTerms(
                product_id=product_id,
                partner_id=8,
                partner_name="Proveedor Hidraulica",
                delay_days=30,
                min_qty=1,
                price=104.16,
                currency="USD",
            )
        ],
    )


def test_a_project_ahead_raises_the_product_forecast() -> None:
    product = _product()
    dataset = PlanningDataset(
        as_of=AS_OF,
        history_start=AS_OF - timedelta(days=120),
        warehouse_id=1,
        warehouse_code="WH",
        products=[product],
    )
    plain = forecast_product(product, dataset)
    project = CalendarEvent(
        kind="project",
        name="Obra",
        product_id=1,
        start_date=AS_OF,
        end_date=AS_OF + timedelta(days=59),
        quantity=600,
    )
    raised = forecast_product(product, dataset, [project])
    assert raised.per_period > plain.per_period
    assert (
        abs((raised.per_period - plain.per_period) / 7 - 10.0) < 0.01
    )  # 600 over 60 days = 10/day
    assert raised.params["calendar_uplift_per_day"] == 10.0


# --- risk ----------------------------------------------------------------------------------


def test_stockout_probability_on_known_distributions() -> None:
    # demand 2/day, sigma 1/day over 30 days: mean 60, sigma 5.48
    assert (
        stockout_probability(position=60, incoming_within=0, daily_mean=2, daily_sigma=1, days=30)
        == 0.5
    )
    assert (
        stockout_probability(position=100, incoming_within=0, daily_mean=2, daily_sigma=1, days=30)
        < 0.01
    )
    assert (
        stockout_probability(position=20, incoming_within=0, daily_mean=2, daily_sigma=1, days=30)
        > 0.99
    )
    # incoming inside the window counts; no variability is a hard edge; no demand is no risk
    assert (
        stockout_probability(position=20, incoming_within=40, daily_mean=2, daily_sigma=1, days=30)
        == 0.5
    )
    assert (
        stockout_probability(position=59, incoming_within=0, daily_mean=2, daily_sigma=0, days=30)
        == 1.0
    )
    assert (
        stockout_probability(position=0, incoming_within=0, daily_mean=0, daily_sigma=0, days=30)
        == 0.0
    )


def test_the_radar_ranks_products_and_counts_late_lines_per_supplier() -> None:
    late = IncomingLine(
        line_id=1,
        product_id=1,
        po_id=77,
        po_name="P00077",
        partner_id=8,
        quantity=40,
        date_planned=AS_OF - timedelta(days=7),
    )
    soon = IncomingLine(
        line_id=2,
        product_id=2,
        po_id=74,
        po_name="P00074",
        partner_id=8,
        quantity=200,
        date_planned=AS_OF + timedelta(days=12),
    )
    far = IncomingLine(
        line_id=3,
        product_id=2,
        po_id=73,
        po_name="P00073",
        partner_id=9,
        quantity=50,
        date_planned=AS_OF + timedelta(days=90),
    )
    risky = _product(1, daily=2.0, on_hand=10.0, incoming=[late])
    safe = _product(2, daily=2.0, on_hand=150.0, incoming=[soon, far])
    dataset = PlanningDataset(
        as_of=AS_OF,
        history_start=AS_OF - timedelta(days=120),
        warehouse_id=1,
        warehouse_code="WH",
        products=[risky, safe],
    )
    forecasts = {p.product_id: forecast_product(p, dataset) for p in dataset.products}
    report = risk_report(
        dataset, forecasts, {8: {"partner_name": "Proveedor Hidraulica", "otif": 0.75}}
    )
    first = report.products[0]
    assert first.product_id == 1 and first.p_stockout_30 > 0.5 and first.late_po_names == ["P00077"]
    assert first.incoming_30 == 40.0
    assert first.suggested_qty == max(0, math.ceil(first.daily_mean * 60 - 10 - 40 - 1e-9))
    second = report.products[1]
    assert (
        second.p_stockout_30 < 0.05 and second.incoming_30 == 200.0 and second.incoming_60 == 200.0
    )
    assert report.at_risk_30 == 1 and report.cash_exposure > 0
    [hidraulica, alterna] = report.suppliers
    assert (
        hidraulica.partner_id == 8 and hidraulica.open_lines == 2 and hidraulica.overdue_lines == 1
    )
    assert hidraulica.expected_late_lines == 1.25  # the overdue one plus 1 - OTIF
    assert (
        alterna.partner_id == 9 and alterna.expected_late_lines == 0.2
    )  # no scorecard: the default
    single = product_risk(risky, forecasts[1], AS_OF)
    assert single.days_of_cover is not None and 0 < single.days_of_cover < 30


def test_supplier_risk_without_open_supply_is_empty() -> None:
    dataset = PlanningDataset(
        as_of=AS_OF,
        history_start=AS_OF - timedelta(days=120),
        warehouse_id=1,
        warehouse_code="WH",
        products=[_product()],
    )
    assert supplier_risks(dataset, {}) == []


# --- consolidation -------------------------------------------------------------------------


def _line(
    product_id: int,
    *,
    order_qty: float,
    position: float,
    order_up_to: float,
    price: float,
    coverage: float | None,
    action: str = "none",
) -> ReplenishmentLine:
    return ReplenishmentLine(
        line_id=f"run:{product_id}",
        product_id=product_id,
        product_ref=f"P{product_id}",
        product_name=f"Product {product_id}",
        warehouse_id=1,
        abc_class="B",
        on_hand=position,
        reserved=0,
        incoming=0,
        position=position,
        forecast_daily=1.0,
        forecast_method="ses",
        history_periods=20,
        sigma_daily=0.2,
        lead_time_days=20,
        sigma_lead_time_days=5,
        service_level=0.95,
        review_period_days=7,
        ss=5,
        rop=25,
        order_up_to=order_up_to,
        coverage_days=coverage,
        proposed_min=25,
        proposed_max=order_up_to,
        order_qty=order_qty,
        supplier_id=8,
        supplier_name="Proveedor Hidraulica",
        unit_price=price,
        currency="USD",
        action=action,  # type: ignore[arg-type]
    )


def test_consolidation_pulls_forward_the_nearest_needs_when_freight_costs_more() -> None:
    # 300 already ordered; +400 due in 5 days; +500 due in 60 days
    ordering = _line(
        1, order_qty=10, position=5, order_up_to=15, price=30.0, coverage=5, action="create_rfq"
    )
    soon = _line(2, order_qty=0, position=20, order_up_to=40, price=20.0, coverage=25)
    later = _line(3, order_qty=0, position=50, order_up_to=60, price=50.0, coverage=80)
    terms = {
        8: FreightTerms(
            partner_id=8,
            partner_name="Proveedor Hidraulica",
            free_freight_over=600,
            freight_cost=80,
        )
    }
    merged = consolidate([ordering, soon, later], terms, holding_pct_year=20.0)
    pulled = [ln for ln in merged if ln.action == "consolidate"]
    assert [ln.product_id for ln in pulled] == [2]  # 300 + 400 reaches 600; product 3 stays
    note = pulled[0].consolidation
    assert note is not None and note.freight_saved == 80 and note.days_early == 5.0
    assert note.stock_cost == round(400 * 0.2 * 5 / 365, 2)
    assert "free-freight" in (pulled[0].explanation or "")
    assert pulled[0].order_qty == 20.0
    assert [ln.action for ln in merged] == ["create_rfq", "consolidate", "none"]


def test_no_consolidation_when_stock_costs_more_than_freight_or_freight_is_free() -> None:
    ordering = _line(
        1, order_qty=10, position=5, order_up_to=15, price=30.0, coverage=5, action="create_rfq"
    )
    # a year early: holding 400 for 360 days costs more than 20 of freight
    expensive = _line(2, order_qty=0, position=20, order_up_to=40, price=20.0, coverage=380)
    terms = {8: FreightTerms(partner_id=8, free_freight_over=600, freight_cost=20)}
    merged = consolidate([ordering, expensive], terms, holding_pct_year=20.0)
    assert all(ln.action != "consolidate" for ln in merged)
    # 900 already on the order: freight is free, nothing to pull forward
    big = _line(
        1, order_qty=30, position=5, order_up_to=35, price=30.0, coverage=5, action="create_rfq"
    )
    assert consolidate([big, expensive], terms, holding_pct_year=20.0) == [big, expensive]
    assert consolidate([ordering, expensive], {}, holding_pct_year=20.0) == [ordering, expensive]
