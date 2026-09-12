"""Pick the forecasting method per series by backtest.

Short series (fewer than ``min_periods``) get a moving average with no
error estimate: there is nothing to validate against. Intermittent series
(at least half the periods without demand) go to Croston, the method made
for them: on such series every method scores badly on WAPE and the metric
cannot tell them apart. Otherwise every candidate is backtested and the
lowest WAPE wins; a candidate within ``TIE_TOLERANCE`` of the best loses to
the simpler one (the order of ``METHODS``). The residual standard deviation
of the chosen method's one-step fits is what the policy uses as demand
variability.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import pstdev

from inventory_planning.forecasting.backtest import Backtest, backtest
from inventory_planning.forecasting.methods import METHODS, Forecast, moving_average

MIN_PERIODS = 8
HORIZON = 4
FOLDS = 8
TIE_TOLERANCE = 0.02  # absolute WAPE points
INTERMITTENT_ZERO_SHARE = 0.5


@dataclass(frozen=True)
class ForecastResult:
    method: str
    per_period: float  # expected demand per period (week) over the horizon
    values: list[float]
    params: dict[str, float]
    sigma: float  # standard deviation of one-step residuals, per period
    wape: float | None
    mape: float | None
    periods: int
    backtests: dict[str, Backtest]

    @property
    def intermittent(self) -> bool:
        return self.method == "croston"


def select_forecast(
    series: Sequence[float],
    *,
    horizon: int = HORIZON,
    min_periods: int = MIN_PERIODS,
    folds: int = FOLDS,
    candidates: Sequence[str] | None = None,
) -> ForecastResult:
    values = [float(v) for v in series]
    if len(values) < min_periods:
        forecast = moving_average(values, horizon)
        return _result(forecast, values, None, None, {})
    names = list(candidates or METHODS)
    if candidates is None and is_intermittent(values):
        names = ["croston"]
    tests = {
        name: backtest(values, METHODS[name], name, horizon=horizon, folds=folds) for name in names
    }
    scored: dict[str, float] = {name: t.wape for name, t in tests.items() if t.wape is not None}
    if not scored:
        forecast = moving_average(values, horizon)
        return _result(forecast, values, None, None, tests)
    best_wape = min(scored.values())
    # simplest method within tolerance of the best (METHODS is ordered simplest first)
    chosen = next(name for name in names if scored.get(name, 9e9) <= best_wape + TIE_TOLERANCE)
    forecast = METHODS[chosen](values, horizon)
    return _result(forecast, values, tests[chosen].wape, tests[chosen].mape, tests)


def is_intermittent(values: Sequence[float]) -> bool:
    """At least half the periods had no demand (and some did)."""
    if not values:
        return False
    zeros = sum(1 for v in values if v <= 0)
    return zeros < len(values) and zeros / len(values) >= INTERMITTENT_ZERO_SHARE


def _result(
    forecast: Forecast,
    values: list[float],
    wape: float | None,
    mape: float | None,
    tests: dict[str, Backtest],
) -> ForecastResult:
    residuals = [y - f for y, f in zip(values[1:], forecast.fitted, strict=False)]
    sigma = (
        pstdev(residuals) if len(residuals) > 1 else (pstdev(values) if len(values) > 1 else 0.0)
    )
    return ForecastResult(
        method=forecast.method,
        per_period=forecast.per_period,
        values=forecast.values,
        params=forecast.params,
        sigma=sigma,
        wape=wape,
        mape=mape,
        periods=len(values),
        backtests=tests,
    )
