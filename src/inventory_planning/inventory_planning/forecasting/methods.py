"""Forecasting methods. Each takes a series of equally spaced periods and a horizon.

* ``moving_average``: mean of the last ``window`` periods, flat ahead.
* ``ses``: simple exponential smoothing; ``alpha`` chosen on a grid by
  in-sample squared error. Flat ahead.
* ``holt``: level plus additive trend (Holt's linear method), ``alpha`` and
  ``beta`` on a grid, trend damped to zero at the floor. Linear ahead.
* ``croston``: intermittent demand (Croston 1972): smooth the non-zero sizes
  and the intervals between them separately; the rate ``size / interval``
  is the flat forecast.
* ``tsb``: Teunter-Syntetos-Babai (2011): smooth the demand probability and
  the size separately, so a product that stops selling decays to zero instead
  of keeping Croston's last rate.
* ``holt_winters_add`` / ``holt_winters_mul``: level, trend and a seasonal
  cycle whose length is found by autocorrelation; additive or multiplicative
  seasonality. Without a detected season they fall back to the moving average.

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


def tsb(series: Series, horizon: int, *, alpha: float = 0.1, beta: float = 0.1) -> Forecast:
    """Teunter-Syntetos-Babai: probability of demand and demand size smoothed apart."""
    if not series:
        return Forecast("tsb", [0.0] * horizon, {"alpha": alpha, "beta": beta})
    nonzero = [y for y in series if y > 0]
    prob = len(nonzero) / len(series)
    size = sum(nonzero) / len(nonzero) if nonzero else 0.0
    fitted: list[float] = []
    for y in series:
        fitted.append(prob * size)
        if y > 0:
            prob = alpha * 1.0 + (1 - alpha) * prob
            size = beta * y + (1 - beta) * size
        else:
            prob = (1 - alpha) * prob
    rate = max(0.0, prob * size)
    return Forecast(
        "tsb", [rate] * horizon, {"alpha": alpha, "beta": beta, "prob": prob, "size": size}, fitted
    )


def detect_season(series: Series, *, min_lag: int = 4, max_lag: int = 52) -> int | None:
    """The seasonal period (in periods) as the strongest autocorrelation peak, or None.

    A lag counts when the series holds at least two full cycles and the
    autocorrelation at that lag is clearly positive (> 0.3) and a local peak.
    """
    n = len(series)
    if n < 2 * min_lag + 2:
        return None
    mean = sum(series) / n
    centred = [y - mean for y in series]
    denominator = sum(c * c for c in centred)
    if denominator <= 1e-12:
        return None

    def acf(lag: int) -> float:
        return sum(centred[i] * centred[i - lag] for i in range(lag, n)) / denominator

    upper = min(max_lag, n // 2)
    values = {lag: acf(lag) for lag in range(1, upper + 1)}
    best: tuple[float, int] | None = None
    for lag in range(min_lag, upper + 1):
        value = values[lag]
        if value <= 0.3:
            continue
        if value < values.get(lag - 1, -1) or value < values.get(lag + 1, -1):
            continue  # not a local peak
        if best is None or value > best[0]:
            best = (value, lag)
    return best[1] if best else None


def holt_winters(
    series: Series,
    horizon: int,
    *,
    seasonal: str = "add",
    season_length: int | None = None,
    alpha: float | None = None,
    beta: float | None = None,
    gamma: float | None = None,
) -> Forecast:
    """Holt-Winters with a detected (or given) season; the moving average without one."""
    m = season_length or detect_season(series)
    name = f"holt_winters_{seasonal}"
    if m is None or len(series) < 2 * m:
        fallback = moving_average(series, horizon)
        return Forecast(
            name, fallback.values, {**fallback.params, "season_length": 0.0}, fallback.fitted
        )
    if seasonal == "mul" and min(series) <= 0:
        fallback = moving_average(series, horizon)
        return Forecast(
            name, fallback.values, {**fallback.params, "season_length": 0.0}, fallback.fitted
        )
    # initial level and trend from the first two cycles, initial season from the first cycle
    first, second = series[:m], series[m : 2 * m]
    level0 = sum(first) / m
    trend0 = (sum(second) - sum(first)) / (m * m)
    if seasonal == "mul":
        season0 = [y / level0 if level0 > 0 else 1.0 for y in first]
    else:
        season0 = [y - level0 for y in first]
    grid = [0.2, 0.5, 0.8]
    best: tuple[float, float, float, float, list[float], float, float, list[float]] | None = None
    for a in [alpha] if alpha is not None else grid:
        for b in [beta] if beta is not None else [0.1, 0.3]:
            for g in [gamma] if gamma is not None else grid:
                level, trend, season = level0, trend0, list(season0)
                fitted: list[float] = []
                sse = 0.0
                for i, y in enumerate(series):
                    s = season[i % m]
                    forecast = (level + trend) * s if seasonal == "mul" else level + trend + s
                    fitted.append(max(0.0, forecast))
                    sse += (y - forecast) ** 2
                    if seasonal == "mul":
                        new_level = a * (y / s if s else y) + (1 - a) * (level + trend)
                        season[i % m] = g * (y / new_level if new_level else s) + (1 - g) * s
                    else:
                        new_level = a * (y - s) + (1 - a) * (level + trend)
                        season[i % m] = g * (y - new_level) + (1 - g) * s
                    trend = b * (new_level - level) + (1 - b) * trend
                    level = new_level
                if best is None or sse < best[0]:
                    best = (sse, a, b, g, fitted, level, trend, season)
    assert best is not None
    _, a, b, g, fitted, level, trend, season = best
    n = len(series)
    values = []
    for h in range(1, horizon + 1):
        s = season[(n + h - 1) % m]
        raw = (level + h * trend) * s if seasonal == "mul" else level + h * trend + s
        values.append(max(0.0, raw))
    return Forecast(
        name,
        values,
        {"alpha": a, "beta": b, "gamma": g, "season_length": float(m), "trend": trend},
        fitted,
    )


def holt_winters_add(series: Series, horizon: int) -> Forecast:
    return holt_winters(series, horizon, seasonal="add")


def holt_winters_mul(series: Series, horizon: int) -> Forecast:
    return holt_winters(series, horizon, seasonal="mul")


Method = Callable[[Series, int], Forecast]

# Simplest first: the selector breaks ties in this order.
METHODS: dict[str, Method] = {
    "moving_average": moving_average,
    "ses": ses,
    "croston": croston,
    "tsb": tsb,
    "holt": holt,
    "holt_winters_add": holt_winters_add,
    "holt_winters_mul": holt_winters_mul,
}
INTERMITTENT_METHODS = ("croston", "tsb")
