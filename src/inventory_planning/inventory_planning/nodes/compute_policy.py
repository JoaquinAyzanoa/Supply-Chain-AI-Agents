"""Turn forecasts, stock and parameters into ``ReplenishmentLine`` numbers.

Pure: same inputs, same line. The exception and action fields are left
for ``detect_exceptions``; here every line starts as ``none``.
"""

from __future__ import annotations

from inventory_planning.forecasting import ForecastResult
from inventory_planning.models import PlanningDataset, ProductData
from inventory_planning.nodes.forecast import daily_rate
from inventory_planning.policy import (
    ProductParams,
    coverage_days,
    order_quantity,
    order_up_to,
    reorder_point,
    safety_stock,
)
from sc_core.schema.planning import ReplenishmentLine


def compute_line(
    product: ProductData,
    forecast: ForecastResult,
    params: ProductParams,
    *,
    run_id: str,
    warehouse_id: int,
    lead_time_sigma_ratio: float,
) -> ReplenishmentLine:
    avg_daily, sigma_daily = daily_rate(forecast)
    supplier = product.preferred_supplier
    promised = float(supplier.delay_days) if supplier else 0.0
    lead_time = params.lead_time_mean_days if params.lead_time_mean_days is not None else promised
    sigma_lt = (
        params.lead_time_sigma_days
        if params.lead_time_sigma_days is not None
        else lead_time_sigma_ratio * lead_time
    )
    # Stored to two decimals; the order quantity is derived from the stored values so a
    # reader recomputes exactly what the line says.
    ss = round(safety_stock(sigma_daily, lead_time, sigma_lt, avg_daily, params.service_level), 2)
    rop = round(reorder_point(avg_daily, lead_time, ss), 2)
    out = round(order_up_to(avg_daily, lead_time, params.review_period_days, ss), 2)
    position = product.on_hand - product.reserved + product.incoming_qty
    moq = supplier.min_qty if supplier else 0.0
    qty = round(order_quantity(position, rop, out, moq=moq), 2) if supplier else 0.0
    rule = product.orderpoint
    return ReplenishmentLine(
        line_id=f"{run_id}:{product.product_id}",
        product_id=product.product_id,
        product_ref=product.ref,
        product_name=product.name,
        warehouse_id=warehouse_id,
        on_hand=product.on_hand,
        reserved=product.reserved,
        incoming=product.incoming_qty,
        position=position,
        forecast_daily=round(avg_daily, 4),
        forecast_method=forecast.method,
        sigma_daily=round(sigma_daily, 4),
        wape=None if forecast.wape is None else round(forecast.wape, 4),
        mape=None if forecast.mape is None else round(forecast.mape, 4),
        history_periods=forecast.periods,
        lead_time_days=lead_time,
        sigma_lead_time_days=round(sigma_lt, 4),
        service_level=params.service_level,
        review_period_days=params.review_period_days,
        abc_class=params.abc_class,
        ss=ss,
        rop=rop,
        order_up_to=out,
        coverage_days=_round_or_none(coverage_days(position, avg_daily)),
        current_min=rule.product_min_qty if rule else None,
        current_max=rule.product_max_qty if rule else None,
        proposed_min=float(_ceil(rop)),
        proposed_max=float(max(_ceil(out), _ceil(rop))),
        order_qty=qty,
        supplier_id=supplier.partner_id if supplier else None,
        supplier_name=supplier.partner_name if supplier else None,
        unit_price=supplier.price if supplier else None,
        currency=supplier.currency if supplier else None,
        moq=moq,
    )


def compute_lines(
    dataset: PlanningDataset,
    forecasts: dict[int, ForecastResult],
    params: dict[int, ProductParams],
    *,
    run_id: str,
    lead_time_sigma_ratio: float,
) -> list[ReplenishmentLine]:
    return [
        compute_line(
            product,
            forecasts[product.product_id],
            params[product.product_id],
            run_id=run_id,
            warehouse_id=dataset.warehouse_id,
            lead_time_sigma_ratio=lead_time_sigma_ratio,
        )
        for product in dataset.products
    ]


def _ceil(value: float) -> int:
    from math import ceil

    return max(0, ceil(value - 1e-9))


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
