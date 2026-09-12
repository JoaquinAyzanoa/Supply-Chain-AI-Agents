"""Forecast methods on known series, selection by backtest, quality on the synthetic fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from inventory_planning.forecasting import (
    METHODS,
    croston,
    holt,
    moving_average,
    select_forecast,
    ses,
)
from inventory_planning.forecasting.backtest import backtest

FIXTURE = Path("tests") / "fixtures" / "planning" / "series.json"
SERIES: dict[str, list[float]] = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_constant_series_is_forecast_exactly_by_every_method() -> None:
    series = [10.0] * 30
    for name, method in METHODS.items():
        forecast = method(series, 4)
        assert forecast.values == pytest.approx([10.0] * 4), name


def test_moving_average_uses_the_last_window() -> None:
    forecast = moving_average([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 2, window=4)
    assert forecast.values == [8.5, 8.5] and forecast.params == {"window": 4.0}
    assert moving_average([], 3).values == [0.0, 0.0, 0.0]


def test_ses_picks_alpha_on_the_grid() -> None:
    forecast = ses([5, 6, 5, 7, 6, 5, 6, 7, 6, 5], 3, alpha=0.5)
    level = 5.0
    for y in [6, 5, 7, 6, 5, 6, 7, 6, 5]:
        level = 0.5 * y + 0.5 * level
    assert forecast.values == pytest.approx([level] * 3) and forecast.params["alpha"] == 0.5
    free = ses(SERIES["stable"], 3)
    assert 0.1 <= free.params["alpha"] <= 0.9


def test_holt_extends_a_linear_trend() -> None:
    series = [float(10 + 2 * i) for i in range(20)]
    forecast = holt(series, 3)
    assert forecast.values == pytest.approx([50, 52, 54], abs=0.5)
    assert forecast.params["trend"] == pytest.approx(2.0, abs=0.1)


def test_croston_rate_is_size_over_interval() -> None:
    series = [0, 0, 6, 0, 0, 6, 0, 0, 6, 0, 0, 6]
    forecast = croston(series, 2, alpha=0.1)
    assert forecast.values == pytest.approx([2.0, 2.0])
    assert croston([0, 0, 0], 2).values == [0.0, 0.0]


def test_backtest_pools_errors_over_folds() -> None:
    series = [10.0] * 20 + [20.0] * 4
    result = backtest(series, moving_average, "moving_average", horizon=4, folds=3, step=4)
    assert result.folds == 3 and result.wape is not None and 0 < result.wape < 1
    assert backtest([0.0] * 12, moving_average, "ma", horizon=2, folds=2).wape is None


@pytest.mark.parametrize(
    ("name", "expected_methods", "max_wape"),
    [
        ("stable", {"moving_average", "ses"}, 0.35),
        ("trending", {"holt", "ses", "moving_average"}, 0.35),
        ("intermittent", {"croston"}, None),
        ("seasonal", {"moving_average", "ses", "holt"}, 0.45),
    ],
)
def test_synthetic_series_select_the_expected_method(
    name: str, expected_methods: set[str], max_wape: float | None
) -> None:
    result = select_forecast(SERIES[name])
    assert result.method in expected_methods, (name, result.method, result.backtests)
    assert result.per_period > 0 and result.sigma >= 0 and result.periods == len(SERIES[name])
    if max_wape is not None:
        assert result.wape is not None and result.wape < max_wape, (name, result.wape)


def test_short_series_uses_moving_average_without_an_error_estimate() -> None:
    result = select_forecast(SERIES["short"])
    assert result.method == "moving_average" and result.mape is None and result.wape is None
    assert result.per_period == pytest.approx(sum(SERIES["short"]) / len(SERIES["short"]))
    assert result.backtests == {}


def test_ties_prefer_the_simpler_method() -> None:
    result = select_forecast([10.0] * 40)  # every method is exact: simplest wins
    assert result.method == "moving_average" and result.wape == 0.0


def test_lowest_wape_wins_when_clearly_better() -> None:
    series = [float(10 + 3 * i) for i in range(40)]  # strong trend: Holt beats the flat methods
    result = select_forecast(series)
    assert result.method == "holt"
    assert result.backtests["holt"].wape < result.backtests["moving_average"].wape


def test_weighted_error_on_the_whole_fixture_is_acceptable() -> None:
    abs_error = actual = 0.0
    for name, series in SERIES.items():
        if name == "short":
            continue
        result = select_forecast(series)
        chosen = result.backtests[result.method]
        abs_error += chosen.abs_error
        actual += chosen.actual
    assert abs_error / actual < 0.35
