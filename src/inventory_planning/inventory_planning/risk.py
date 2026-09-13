"""The risk radar: what may run out, what may arrive late, and what money is on the line.

Per product, the probability of a stockout within 30 and 60 days from the
forecast distribution, counting only the open supply due inside the window.
Per supplier, the expected late lines among the open supply: an overdue
line counts as late, a line still due counts ``1 - OTIF`` (or a default when
the supplier has no history). Cash exposure is the open supply valued at
its order price. Everything here is arithmetic on the planner's dataset.
"""

from __future__ import annotations

from datetime import date, timedelta
from math import ceil
from typing import Any

from pydantic import Field

from inventory_planning.forecasting import ForecastResult
from inventory_planning.models import PlanningDataset, ProductData
from inventory_planning.nodes.forecast import daily_rate
from sc_core.schema.base import StrictModel
from sc_core.shared.stats import stockout_probability

HORIZONS = (30, 60)
DEFAULT_LATE_RATE = 0.2  # a supplier without a scorecard yet


class ProductRisk(StrictModel):
    product_id: int
    product_ref: str
    product_name: str
    category: str | None = None
    on_hand: float
    reserved: float
    position: float = Field(description="on hand - reserved (what can ship today)")
    incoming_30: float
    incoming_60: float
    daily_mean: float
    daily_sigma: float
    forecast_method: str
    days_of_cover: float | None = None
    p_stockout_30: float = Field(ge=0, le=1)
    p_stockout_60: float = Field(ge=0, le=1)
    open_po_names: list[str] = Field(default_factory=list)
    late_po_names: list[str] = Field(default_factory=list)
    suggested_qty: float = Field(ge=0, description="to cover 60 days beyond what is coming")
    exposure: float = Field(ge=0, description="open supply at order price")
    unit_price: float | None = None


class SupplierRisk(StrictModel):
    partner_id: int
    partner_name: str
    open_lines: int
    overdue_lines: int
    expected_late_lines: float
    otif: float | None = None
    lead_time_sigma_days: float | None = None
    exposure: float = Field(ge=0)


class RiskReport(StrictModel):
    as_of: date
    warehouse_code: str
    products: list[ProductRisk] = Field(default_factory=list)
    suppliers: list[SupplierRisk] = Field(default_factory=list)
    cash_exposure: float = 0.0
    at_risk_30: int = 0  # products over the 50% line at 30 days


def product_risk(
    product: ProductData, forecast: ForecastResult, as_of: date, *, uplift_per_day: float = 0.0
) -> ProductRisk:
    mean, sigma = daily_rate(forecast)
    mean = max(0.0, mean + uplift_per_day)
    position = product.on_hand - product.reserved
    incoming_30 = _incoming_within(product, as_of, 30)
    incoming_60 = _incoming_within(product, as_of, 60)
    price = product.preferred_supplier.price if product.preferred_supplier else None
    exposure = sum(line.quantity * (price or product.standard_price) for line in product.incoming)
    cover = (position + product.incoming_qty) / mean if mean > 0 else None
    return ProductRisk(
        product_id=product.product_id,
        product_ref=product.ref,
        product_name=product.name,
        category=product.category,
        on_hand=product.on_hand,
        reserved=product.reserved,
        position=round(position, 2),
        incoming_30=incoming_30,
        incoming_60=incoming_60,
        daily_mean=round(mean, 4),
        daily_sigma=round(sigma, 4),
        forecast_method=forecast.method,
        days_of_cover=round(cover, 1) if cover is not None else None,
        p_stockout_30=stockout_probability(
            position=position,
            incoming_within=incoming_30,
            daily_mean=mean,
            daily_sigma=sigma,
            days=30,
        ),
        p_stockout_60=stockout_probability(
            position=position,
            incoming_within=incoming_60,
            daily_mean=mean,
            daily_sigma=sigma,
            days=60,
        ),
        open_po_names=sorted({line.po_name for line in product.incoming}),
        late_po_names=sorted(
            {
                line.po_name
                for line in product.incoming
                if line.date_planned is not None and line.date_planned < as_of
            }
        ),
        suggested_qty=float(max(0, ceil(mean * 60 - position - incoming_60 - 1e-9))),
        exposure=round(exposure, 2),
        unit_price=price,
    )


def _incoming_within(product: ProductData, as_of: date, days: int) -> float:
    end = as_of + timedelta(days=days)
    return sum(
        line.quantity
        for line in product.incoming
        if line.date_planned is None or line.date_planned <= end
    )


def supplier_risks(
    dataset: PlanningDataset, scores: dict[int, dict[str, Any]]
) -> list[SupplierRisk]:
    """Expected late lines per supplier from the open supply and the scorecards."""
    by_partner: dict[int, dict[str, Any]] = {}
    names = {
        terms.partner_id: terms.partner_name for p in dataset.products for terms in p.suppliers
    }
    prices = {
        (terms.partner_id, terms.product_id): terms.price
        for p in dataset.products
        for terms in p.suppliers
    }
    for product in dataset.products:
        for line in product.incoming:
            if line.partner_id is None:
                continue
            entry = by_partner.setdefault(
                line.partner_id, {"open": 0, "overdue": 0, "late": 0.0, "exposure": 0.0}
            )
            score = scores.get(line.partner_id) or {}
            otif = score.get("otif")
            late_rate = (1.0 - float(otif)) if otif is not None else DEFAULT_LATE_RATE
            overdue = line.date_planned is not None and line.date_planned < dataset.as_of
            entry["open"] += 1
            entry["overdue"] += int(overdue)
            entry["late"] += 1.0 if overdue else late_rate
            price = prices.get((line.partner_id, product.product_id), product.standard_price)
            entry["exposure"] += line.quantity * price
    out: list[SupplierRisk] = []
    for partner_id, entry in by_partner.items():
        score = scores.get(partner_id) or {}
        out.append(
            SupplierRisk(
                partner_id=partner_id,
                partner_name=str(score.get("partner_name") or names.get(partner_id) or partner_id),
                open_lines=entry["open"],
                overdue_lines=entry["overdue"],
                expected_late_lines=round(entry["late"], 2),
                otif=score.get("otif"),
                lead_time_sigma_days=score.get("lead_time_sigma_days"),
                exposure=round(entry["exposure"], 2),
            )
        )
    return sorted(out, key=lambda s: (-s.expected_late_lines, s.partner_name))


def risk_report(
    dataset: PlanningDataset,
    forecasts: dict[int, ForecastResult],
    scores: dict[int, dict[str, Any]],
    *,
    uplifts: dict[int, float] | None = None,
) -> RiskReport:
    products = [
        product_risk(
            p,
            forecasts[p.product_id],
            dataset.as_of,
            uplift_per_day=(uplifts or {}).get(p.product_id, 0.0),
        )
        for p in dataset.products
        if p.product_id in forecasts
    ]
    products.sort(key=lambda r: (-r.p_stockout_30, -r.p_stockout_60, r.product_ref))
    suppliers = supplier_risks(dataset, scores)
    return RiskReport(
        as_of=dataset.as_of,
        warehouse_code=dataset.warehouse_code,
        products=products,
        suppliers=suppliers,
        cash_exposure=round(sum(r.exposure for r in products), 2),
        at_risk_30=sum(1 for r in products if r.p_stockout_30 >= 0.5),
    )


__all__ = [
    "HORIZONS",
    "ProductRisk",
    "RiskReport",
    "SupplierRisk",
    "product_risk",
    "risk_report",
    "supplier_risks",
]
