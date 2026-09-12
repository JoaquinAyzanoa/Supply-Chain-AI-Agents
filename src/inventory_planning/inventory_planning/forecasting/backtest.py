"""Rolling-origin backtest.

For each of the last ``folds`` origins (``step`` periods apart) the method
is fitted on everything before the origin and asked for ``horizon`` periods;
the errors against what actually happened are pooled. WAPE (sum of absolute
errors over sum of actuals) is the selection metric: it is defined for
series with zeros, which MAPE is not. MAPE is reported over the non-zero
actuals for readers used to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from inventory_planning.forecasting.methods import Method


@dataclass(frozen=True)
class Backtest:
    method: str
    wape: float | None  # None when there was nothing to compare against
    mape: float | None
    folds: int
    abs_error: float
    actual: float


def backtest(
    series: Sequence[float], method: Method, name: str, *, horizon: int, folds: int, step: int = 1
) -> Backtest:
    n = len(series)
    origins = [n - horizon - i * step for i in range(folds)]
    origins = [o for o in origins if o >= max(4, horizon)]
    abs_error = actual = 0.0
    pct: list[float] = []
    used = 0
    for origin in sorted(origins):
        forecast = method(series[:origin], horizon)
        truth = series[origin : origin + horizon]
        for y, f in zip(truth, forecast.values, strict=False):
            abs_error += abs(y - f)
            actual += abs(y)
            if y > 0:
                pct.append(abs(y - f) / y)
        used += 1
    wape = abs_error / actual if actual > 0 else None
    mape = sum(pct) / len(pct) if pct else None
    return Backtest(name, wape, mape, used, abs_error, actual)
