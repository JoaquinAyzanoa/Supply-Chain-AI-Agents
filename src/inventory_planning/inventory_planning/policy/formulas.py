"""Safety stock, reorder point, order-up-to level and order quantity.

Standard continuous-review formulas with a review period, in daily units:

* ``SS  = z * sqrt(LT * sigma_d^2 + d^2 * sigma_LT^2)``  (demand and lead-time variability)
* ``ROP = d * LT + SS``                                   (reorder point = proposed minimum)
* ``OUT = d * (LT + R) + SS``                              (order-up-to = proposed maximum)
* order when ``position < ROP``: ``qty = max(OUT - position, MOQ)`` rounded up to the pack multiple

``d`` is the average daily demand, ``LT`` the lead time in days, ``R`` the
review period in days, ``z`` the normal quantile of the service level.
Every function is pure so a line's numbers can be recomputed by hand from
the inputs stored with it.
"""

from __future__ import annotations

from math import ceil, sqrt
from statistics import NormalDist


def z_for(service_level: float) -> float:
    """Normal quantile for a cycle service level (0.95 -> 1.645)."""
    if not 0.0 < service_level < 1.0:
        raise ValueError("service level must be strictly between 0 and 1")
    return NormalDist().inv_cdf(service_level)


def safety_stock(
    sigma_daily_demand: float,
    lead_time_days: float,
    sigma_lead_time_days: float,
    avg_daily_demand: float,
    service_level: float,
) -> float:
    """Combined demand and lead-time variability: ``z * sqrt(LT * sd^2 + d^2 * sLT^2)``."""
    if lead_time_days < 0 or sigma_daily_demand < 0 or sigma_lead_time_days < 0:
        raise ValueError("lead time and standard deviations cannot be negative")
    variance = (
        lead_time_days * sigma_daily_demand**2 + avg_daily_demand**2 * sigma_lead_time_days**2
    )
    return z_for(service_level) * sqrt(variance)


def reorder_point(avg_daily_demand: float, lead_time_days: float, ss: float) -> float:
    return avg_daily_demand * lead_time_days + ss


def order_up_to(
    avg_daily_demand: float, lead_time_days: float, review_period_days: float, ss: float
) -> float:
    return avg_daily_demand * (lead_time_days + review_period_days) + ss


def order_quantity(
    projected_position: float,
    rop: float,
    order_up_to_level: float,
    moq: float = 0.0,
    multiple: float = 0.0,
) -> float:
    """Order when the position (on hand + incoming - committed) is below the ROP.

    The raw quantity brings the position back to the order-up-to level; it
    is raised to the supplier's minimum and rounded up to the pack multiple.
    """
    if projected_position >= rop:
        return 0.0
    raw = order_up_to_level - projected_position
    qty = max(raw, moq)
    if multiple and multiple > 0:
        qty = ceil(qty / multiple - 1e-9) * multiple
    return float(qty)


def coverage_days(projected_position: float, avg_daily_demand: float) -> float | None:
    """Days the position covers at the average rate; ``None`` when there is no demand."""
    if avg_daily_demand <= 0:
        return None
    return max(0.0, projected_position) / avg_daily_demand
