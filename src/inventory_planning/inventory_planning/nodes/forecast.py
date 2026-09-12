"""Forecast every product on weekly buckets; convert to the daily rate the policy uses."""

from __future__ import annotations

from datetime import timedelta
from math import sqrt

from inventory_planning.forecasting import ForecastResult, select_forecast
from inventory_planning.models import PlanningDataset, ProductData

DAYS_PER_PERIOD = 7


def forecast_product(product: ProductData, dataset: PlanningDataset) -> ForecastResult:
    end = dataset.as_of - timedelta(days=1)
    return select_forecast(product.weekly_demand(dataset.history_start, end))


def forecast_all(dataset: PlanningDataset) -> dict[int, ForecastResult]:
    return {p.product_id: forecast_product(p, dataset) for p in dataset.products}


def daily_rate(result: ForecastResult) -> tuple[float, float]:
    """(average daily demand, daily sigma) from a weekly forecast: sigma scales with sqrt(7)."""
    return result.per_period / DAYS_PER_PERIOD, result.sigma / sqrt(DAYS_PER_PERIOD)
