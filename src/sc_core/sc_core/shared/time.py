"""Time helpers.

Policy: everything stored, logged or exchanged is timezone-aware UTC.
Conversion to the business timezone (Lima by default) happens only at the
edges: rendering for humans and interpreting dates typed by humans.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ_NAME = "America/Lima"
LIMA = ZoneInfo(DEFAULT_TZ_NAME)


def utc_now() -> datetime:
    """Current time, timezone-aware UTC."""
    return datetime.now(UTC)


def ensure_aware(value: datetime, *, assume: ZoneInfo | None = None) -> datetime:
    """Return an aware datetime. Naive input is interpreted as ``assume`` (UTC by default).

    Odoo returns naive UTC strings; Graph returns ISO strings with ``Z``.
    Both end up as aware UTC through this function.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=assume or UTC)
    return value


def to_utc(value: datetime, *, assume: ZoneInfo | None = None) -> datetime:
    return ensure_aware(value, assume=assume).astimezone(UTC)


def to_local(value: datetime, tz: ZoneInfo = LIMA) -> datetime:
    return ensure_aware(value).astimezone(tz)


def local_today(tz: ZoneInfo = LIMA, *, now: datetime | None = None) -> date:
    return to_local(now or utc_now(), tz).date()


def parse_iso(value: str, *, assume: ZoneInfo | None = None) -> datetime:
    """Parse ISO 8601 (accepting a trailing ``Z``) into aware UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return to_utc(datetime.fromisoformat(text), assume=assume)


def iso_utc(value: datetime) -> str:
    """Canonical serialisation: UTC, seconds precision, ``Z`` suffix."""
    return to_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_business_day(day: date) -> bool:
    return day.weekday() < 5


def add_business_days(start: date, days: int) -> date:
    """Move ``days`` business days forward (or backward when negative), Mon–Fri only.

    Holidays are not modelled; the follow-up policy treats them as slack.
    """
    step = 1 if days >= 0 else -1
    remaining = abs(days)
    current = start
    while remaining:
        current += timedelta(days=step)
        if is_business_day(current):
            remaining -= 1
    return current


def business_days_between(start: date, end: date) -> int:
    """Number of business days strictly after ``start`` up to and including ``end``.

    ``business_days_between(d, d) == 0``; a Friday to the following Monday is 1.
    Negative when ``end`` precedes ``start``.
    """
    if end < start:
        return -business_days_between(end, start)
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if is_business_day(current):
            count += 1
    return count
