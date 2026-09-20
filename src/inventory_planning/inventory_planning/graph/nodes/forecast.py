"""Forecast every product on weekly buckets; convert to the daily rate the policy uses."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from math import sqrt

from inventory_planning.domain.forecasting import ForecastResult, select_forecast
from inventory_planning.domain.forecasting.calendar import (
    adjust_history,
    events_for,
    forecast_uplift,
)
from inventory_planning.domain.models import PlanningDataset, ProductData
from sc_core.schema.calendar import CalendarEvent

DAYS_PER_PERIOD = 7


HORIZON_DAYS = 60  # how far ahead the calendar's events raise the rate


def forecast_product(
    product: ProductData, dataset: PlanningDataset, events: list[CalendarEvent] | None = None
) -> ForecastResult:
    end = dataset.as_of - timedelta(days=1)
    mine = events_for(events or [], product.product_id, product.category)
    series = adjust_history(
        product.weekly_demand(dataset.history_start, end), dataset.history_start, mine, end=end
    )
    result = select_forecast(series)
    if not mine:
        return result
    # the events ahead: raise (or cut) the weekly rate over the planning horizon
    extra = forecast_uplift(result.per_period / DAYS_PER_PERIOD, dataset.as_of, HORIZON_DAYS, mine)
    per_period = max(0.0, result.per_period + extra * DAYS_PER_PERIOD)
    return replace(
        result,
        per_period=per_period,
        values=[max(0.0, v + extra * DAYS_PER_PERIOD) for v in result.values],
        params={**result.params, "calendar_uplift_per_day": round(extra, 4)},
    )


def forecast_all(
    dataset: PlanningDataset, events: list[CalendarEvent] | None = None
) -> dict[int, ForecastResult]:
    return {p.product_id: forecast_product(p, dataset, events) for p in dataset.products}


def daily_rate(result: ForecastResult) -> tuple[float, float]:
    """(average daily demand, daily sigma) from a weekly forecast: sigma scales with sqrt(7)."""
    return result.per_period / DAYS_PER_PERIOD, result.sigma / sqrt(DAYS_PER_PERIOD)
