"""Policy formulas with known values, ABC defaults, and lines recomputable by hand."""

from __future__ import annotations

from datetime import date
from math import sqrt

import pytest

from inventory_planning.forecasting import ForecastResult
from inventory_planning.nodes.compute_policy import compute_line, compute_lines
from inventory_planning.nodes.forecast import daily_rate, forecast_all
from inventory_planning.nodes.load_data import load_dataset
from inventory_planning.policy import (
    MemoryParamsStore,
    ParamsStore,
    ProductParams,
    abc_classes,
    coverage_days,
    order_quantity,
    order_up_to,
    reorder_point,
    safety_stock,
    z_for,
)
from inventory_planning.policy.params import resolve_params
from inventory_planning.testing import demo_ports

AS_OF = date(2026, 9, 14)


def test_z_for_common_service_levels() -> None:
    assert z_for(0.95) == pytest.approx(1.6449, abs=1e-3)
    assert z_for(0.975) == pytest.approx(1.96, abs=1e-3)
    with pytest.raises(ValueError):
        z_for(1.0)


def test_safety_stock_without_lead_time_variability() -> None:
    # z * sigma_d * sqrt(LT): 1.645 * 2 * sqrt(25) = 16.45
    assert safety_stock(2.0, 25, 0.0, 5.0, 0.95) == pytest.approx(1.6449 * 2 * 5, abs=0.02)


def test_safety_stock_combines_both_variabilities() -> None:
    ss = safety_stock(2.0, 25, 4.0, 5.0, 0.95)
    assert ss == pytest.approx(1.6449 * sqrt(25 * 4 + 25 * 16), abs=0.05)
    with pytest.raises(ValueError):
        safety_stock(-1, 25, 0, 5, 0.95)


def test_reorder_point_and_order_up_to() -> None:
    assert reorder_point(5.0, 25, 16.45) == pytest.approx(141.45)
    assert order_up_to(5.0, 25, 7, 16.45) == pytest.approx(176.45)


@pytest.mark.parametrize(
    ("position", "rop", "out", "moq", "multiple", "expected"),
    [
        (150, 141.45, 176.45, 0, 0, 0.0),  # above the ROP: nothing
        (100, 141.45, 176.45, 0, 0, 76.45),  # back to order-up-to
        (100, 141.45, 176.45, 100, 0, 100.0),  # raised to the MOQ
        (100, 141.45, 176.45, 0, 10, 80.0),  # rounded up to the pack
        (100, 141.45, 176.45, 85, 10, 90.0),  # MOQ then pack
        (-20, 141.45, 176.45, 0, 0, 196.45),  # backorders make the position negative
    ],
)
def test_order_quantity(
    position: float, rop: float, out: float, moq: float, multiple: float, expected: float
) -> None:
    assert order_quantity(position, rop, out, moq, multiple) == pytest.approx(expected)


def test_no_demand_means_no_order_and_no_coverage() -> None:
    assert order_quantity(10, reorder_point(0, 30, 0), order_up_to(0, 30, 7, 0)) == 0.0
    assert coverage_days(10, 0) is None and coverage_days(-5, 2) == 0.0
    assert coverage_days(30, 2) == 15.0


def test_abc_classes_by_cumulative_revenue() -> None:
    revenue = {1: 500.0, 2: 300.0, 3: 120.0, 4: 60.0, 5: 20.0, 6: 0.0}
    classes = abc_classes(revenue)
    assert classes == {1: "A", 2: "A", 3: "B", 4: "C", 5: "C", 6: "C"}
    assert abc_classes({}) == {}


async def test_params_resolve_stored_over_defaults() -> None:
    store = MemoryParamsStore()
    assert isinstance(store, ParamsStore)
    await store.save(
        ProductParams(
            product_id=1,
            abc_class="A",
            service_level=0.99,
            review_period_days=3,
            max_coverage_days=60,
            source="planner",
        )
    )
    resolved = resolve_params([1, 2], await store.for_products([1, 2]), {1: "A", 2: "C"})
    assert resolved[1].service_level == 0.99 and resolved[1].source == "planner"
    assert resolved[2] == ProductParams.default_for(2, "C")
    assert resolved[2].service_level == 0.90 and resolved[2].review_period_days == 14


def _forecast(per_week: float, sigma_week: float) -> ForecastResult:
    return ForecastResult(
        method="ses",
        per_period=per_week,
        values=[per_week] * 4,
        params={"alpha": 0.3},
        sigma=sigma_week,
        wape=0.2,
        mape=0.25,
        periods=104,
        backtests={},
    )


async def test_line_numbers_recompute_by_hand() -> None:
    ports = demo_ports(as_of=AS_OF)
    ds = await load_dataset(ports, as_of=AS_OF, history_days=730)
    cbea = ds.product(1)
    assert cbea is not None
    params = ProductParams.default_for(1, "B")  # 0.95, review 7 days
    forecast = _forecast(per_week=7.0, sigma_week=2.1)
    line = compute_line(
        cbea, forecast, params, run_id="run_1", warehouse_id=1, lead_time_sigma_ratio=0.25
    )
    d, sd = daily_rate(forecast)
    assert (d, sd) == (pytest.approx(1.0), pytest.approx(2.1 / sqrt(7)))
    lt, s_lt = 30.0, 7.5  # promised delay and 25 percent of it
    ss = round(z_for(0.95) * sqrt(lt * sd**2 + d**2 * s_lt**2), 2)
    assert line.ss == ss
    assert line.rop == round(d * lt + ss, 2)
    assert line.order_up_to == round(d * (lt + 7) + ss, 2)
    assert line.position == 20 - 2 + 10 and line.incoming == 10
    assert line.order_qty == round(order_quantity(28, line.rop, line.order_up_to, moq=1), 2)
    assert line.proposed_min == 45.0 and line.proposed_max == 52.0  # ceil(44.26), ceil(51.26)
    assert line.current_min == 4 and line.current_max == 56
    assert line.supplier_id == 20 and line.unit_price == 104.0 and line.currency == "PEN"
    assert line.coverage_days == 28.0 and line.action == "none" and line.exception is None
    assert line.line_id == "run_1:1" and line.abc_class == "B"


async def test_measured_lead_time_overrides_the_promise() -> None:
    ports = demo_ports(as_of=AS_OF)
    ds = await load_dataset(ports, as_of=AS_OF, history_days=730)
    cbea = ds.product(1)
    assert cbea is not None
    params = ProductParams(
        product_id=1,
        abc_class="A",
        service_level=0.97,
        review_period_days=7,
        max_coverage_days=90,
        lead_time_mean_days=36.0,
        lead_time_sigma_days=5.0,
        source="measured",
    )
    line = compute_line(
        cbea, _forecast(7.0, 2.1), params, run_id="r", warehouse_id=1, lead_time_sigma_ratio=0.25
    )
    assert line.lead_time_days == 36.0 and line.sigma_lead_time_days == 5.0


async def test_product_without_supplier_has_no_order() -> None:
    ports = demo_ports(as_of=AS_OF)
    ds = await load_dataset(ports, as_of=AS_OF, history_days=730)
    forecasts = forecast_all(ds)
    params = {p.product_id: ProductParams.default_for(p.product_id, "C") for p in ds.products}
    lines = compute_lines(ds, forecasts, params, run_id="run_1", lead_time_sigma_ratio=0.25)
    assert [ln.product_ref for ln in lines] == ["CBEA-LHN", "CXDA-XCN", "LODC-XDN", "990-011-007"]
    lodc = next(ln for ln in lines if ln.product_ref == "LODC-XDN")
    assert lodc.supplier_id is None and lodc.order_qty == 0.0 and lodc.lead_time_days == 0.0
    assert lodc.current_min is None and lodc.proposed_min == 0.0
    kit = next(ln for ln in lines if ln.product_ref == "990-011-007")
    assert kit.coverage_days is not None and kit.coverage_days > 150  # 400 on hand: overstock
    assert all(ln.forecast_method in ("moving_average", "ses", "holt", "croston") for ln in lines)


def test_runtime_planning_defaults_replace_class_defaults_but_not_tuned_params() -> None:
    from inventory_planning.nodes.propose import _runtime_defaults
    from sc_core.schema.runtime_settings import RuntimeSettings

    untouched = ProductParams.default_for(1, "A")
    tuned = untouched.model_copy(update={"source": "planner", "service_level": 0.9})
    runtime = RuntimeSettings(planning_service_level=0.93, planning_max_coverage_days=60)
    changed = _runtime_defaults(untouched, runtime)
    assert changed.service_level == 0.93 and changed.max_coverage_days == 60
    assert changed.review_period_days == untouched.review_period_days  # None keeps the class value
    assert _runtime_defaults(tuned, runtime) is tuned
    assert _runtime_defaults(untouched, RuntimeSettings()) is untouched
