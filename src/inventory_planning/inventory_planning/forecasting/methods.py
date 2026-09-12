"""Forecasting methods. Each takes a series of equally spaced periods and a horizon.

* ``moving_average``: mean of the last ``window`` periods, flat ahead.
* ``ses``: simple exponential smoothing; ``alpha`` chosen on a grid by
  in-sample squared error. Flat ahead.
* ``holt``: level plus additive trend (Holt's linear method), ``alpha`` and
  ``beta`` on a grid, trend damped to zero at the floor. Linear ahead.
* ``croston``: intermittent demand (Croston 1972): smooth the non-zero sizes
  and the intervals between them separately; the rate ``size / interval``
  is the flat forecast.

Parameters are chosen by grid search, not by a library optimiser, so every
number can be recomputed by hand from the stored ``params``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

Series = Sequence[float]
GRID = [round(0.1 * i, 1) for i in range(1, 10)]  # 0.1 .. 0.9


@dataclass(frozen=True)
class Forecast:
    method: str
    values: list[float]  # one per future period
    params: dict[str, float] = field(default_factory=dict)
    fitted: list[float] = field(default_factory=list)  # one-step-ahead fits, for residuals

    @property
    def per_period(self) -> float:
        return sum(self.values) / len(self.values) if self.values else 0.0


def moving_average(series: Series, horizon: int, *, window: int = 8) -> Forecast:
    if not series:
        return Forecast("moving_average", [0.0] * horizon, {"window": float(window)})
    window = min(window, len(series))
    level = sum(series[-window:]) / window
    fitted = [
        sum(series[max(0, i - window) : i]) / max(1, min(i, window)) for i in range(1, len(series))
    ]
    return Forecast(
        "moving_average", [max(0.0, level)] * horizon, {"window": float(window)}, fitted
    )


def ses(series: Series, horizon: int, *, alpha: float | None = None) -> Forecast:
    if not series:
        return Forecast("ses", [0.0] * horizon, {"alpha": alpha or 0.0})
    best_alpha, best_sse, best_fit = 0.0, float("inf"), [0.0]
    for a in [alpha] if alpha is not None else GRID:
        level = series[0]
        fitted = []
        sse = 0.0
        for y in series[1:]:
            fitted.append(level)
            sse += (y - level) ** 2
            level = a * y + (1 - a) * level
        if sse < best_sse:
            best_alpha, best_sse, best_fit = a, sse, fitted + [level]
    level = best_fit[-1]
    return Forecast("ses", [max(0.0, level)] * horizon, {"alpha": best_alpha}, best_fit[:-1])


def holt(
    series: Series, horizon: int, *, alpha: float | None = None, beta: float | None = None
) -> Forecast:
    if len(series) < 3:
        return moving_average(series, horizon)
    best: tuple[float, float, float, float, list[float]] | None = None  # sse, a, b, level, trend
    for a in [alpha] if alpha is not None else GRID:
        for b in [beta] if beta is not None else GRID:
            level, trend = series[0], series[1] - series[0]
            fitted: list[float] = []
            sse = 0.0
            for y in series[1:]:
                forecast = level + trend
                fitted.append(forecast)
                sse += (y - forecast) ** 2
                new_level = a * y + (1 - a) * (level + trend)
                trend = b * (new_level - level) + (1 - b) * trend
                level = new_level
            if best is None or sse < best[0]:
                best = (sse, a, b, level, [trend, *fitted])
    assert best is not None
    _, a, b, level, rest = best
    trend, fitted = rest[0], rest[1:]
    values = [max(0.0, level + h * trend) for h in range(1, horizon + 1)]
    return Forecast("holt", values, {"alpha": a, "beta": b, "trend": trend}, fitted)


def croston(series: Series, horizon: int, *, alpha: float = 0.1) -> Forecast:
    first = next((i for i, y in enumerate(series) if y > 0), None)
    if first is None:
        return Forecast("croston", [0.0] * horizon, {"alpha": alpha})
    size, interval = float(series[first]), float(first + 1)
    rate = size / interval
    fitted: list[float] = [rate] * first  # before the first demand the rate is undefined: use it
    gap = 0
    for y in series[first + 1 :]:
        fitted.append(rate)
        gap += 1
        if y > 0:
            size = alpha * y + (1 - alpha) * size
            interval = alpha * gap + (1 - alpha) * interval
            rate = size / interval
            gap = 0
    return Forecast(
        "croston",
        [max(0.0, rate)] * horizon,
        {"alpha": alpha, "size": size, "interval": interval},
        fitted,
    )


Method = Callable[[Series, int], Forecast]

# Simplest first: the selector breaks ties in this order.
METHODS: dict[str, Method] = {
    "moving_average": moving_average,
    "ses": ses,
    "croston": croston,
    "holt": holt,
}
