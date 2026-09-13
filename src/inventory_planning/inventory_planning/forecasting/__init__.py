"""Statistical forecasting on weekly buckets, in plain Python.

Four methods (``methods``), a rolling-origin backtest (``backtest``) and a
selector that keeps the method with the lowest weighted error and prefers
the simpler one on ties (``select``). No model is involved anywhere here.
"""

from inventory_planning.forecasting.methods import (
    INTERMITTENT_METHODS,
    METHODS,
    Forecast,
    croston,
    detect_season,
    holt,
    holt_winters,
    moving_average,
    ses,
    tsb,
)
from inventory_planning.forecasting.select import ForecastResult, select_forecast

__all__ = [
    "INTERMITTENT_METHODS",
    "METHODS",
    "detect_season",
    "holt_winters",
    "tsb",
    "Forecast",
    "ForecastResult",
    "croston",
    "holt",
    "moving_average",
    "select_forecast",
    "ses",
]
