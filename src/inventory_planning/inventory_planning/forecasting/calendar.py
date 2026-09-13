"""Apply the demand calendar to a series and to a forecast.

History: a week inside a promotion (factor 1.5) is divided by 1.5, a holiday
week (0.5) is doubled, so the methods learn the normal level; project units
are subtracted from the weeks they landed in. Forecast: the daily rate over
the horizon is raised by the events ahead, weighted by the days they cover,
and a project adds its units spread over its own days.
"""

from __future__ import annotations

from datetime import date, timedelta

from sc_core.schema.calendar import CalendarEvent

PERIOD_DAYS = 7


def events_for(
    events: list[CalendarEvent], product_id: int, category: str | None
) -> list[CalendarEvent]:
    return [e for e in events if e.applies_to(product_id, category)]


def adjust_history(
    buckets: list[float], start: date, events: list[CalendarEvent], *, end: date | None = None
) -> list[float]:
    """Normalise weekly buckets (first week starting at ``start``) for past events; the
    last bucket stops at ``end`` so nothing beyond the history is counted."""
    if not events:
        return list(buckets)
    out: list[float] = []
    for index, value in enumerate(buckets):
        week_start = start + timedelta(days=index * PERIOD_DAYS)
        week_end = week_start + timedelta(days=PERIOD_DAYS - 1)
        if end is not None and week_end > end:
            week_end = end
        adjusted = value
        for event in events:
            overlap = event.overlap_days(week_start, week_end)
            if overlap <= 0:
                continue
            share = overlap / PERIOD_DAYS
            if event.kind == "project":
                adjusted -= event.quantity * (overlap / event.days)
            elif event.factor != 1.0:
                # only the covered share of the week was inflated or cut
                adjusted = adjusted / (1.0 + share * (event.factor - 1.0))
        out.append(max(0.0, adjusted))
    return out


def forecast_uplift(
    daily_rate: float, as_of: date, horizon_days: int, events: list[CalendarEvent]
) -> float:
    """Extra units per day over the horizon from the events ahead (may be negative)."""
    if horizon_days <= 0 or not events:
        return 0.0
    end = as_of + timedelta(days=horizon_days - 1)
    extra = 0.0
    for event in events:
        overlap = event.overlap_days(as_of, end)
        if overlap <= 0:
            continue
        if event.kind == "project":
            extra += event.quantity * (overlap / event.days)
        elif event.factor != 1.0:
            extra += daily_rate * (event.factor - 1.0) * overlap
    return extra / horizon_days


__all__ = ["adjust_history", "events_for", "forecast_uplift"]
