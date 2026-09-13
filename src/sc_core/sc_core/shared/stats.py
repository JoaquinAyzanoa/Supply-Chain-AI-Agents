"""Small statistics used by the planner and the Control Tower, in plain Python."""

from __future__ import annotations

from math import erf, sqrt


def normal_cdf(x: float) -> float:
    """P(Z <= x) for a standard normal variable."""
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def stockout_probability(
    *,
    position: float,
    incoming_within: float,
    daily_mean: float,
    daily_sigma: float,
    days: int,
) -> float:
    """P(demand over ``days`` exceeds what we have plus what arrives in that window).

    Demand over the window is taken as normal with mean ``daily_mean * days`` and
    standard deviation ``daily_sigma * sqrt(days)`` (independent days). With no
    variability the answer is 0 or 1; with no demand it is 0.
    """
    if days <= 0 or daily_mean <= 0 and daily_sigma <= 0:
        return 0.0
    available = max(0.0, position) + max(0.0, incoming_within)
    mean = daily_mean * days
    sigma = daily_sigma * sqrt(days)
    if sigma <= 1e-9:
        return 1.0 if mean > available else 0.0
    return round(1.0 - normal_cdf((available - mean) / sigma), 4)


__all__ = ["normal_cdf", "stockout_probability"]
