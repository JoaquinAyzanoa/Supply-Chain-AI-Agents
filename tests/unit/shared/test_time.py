"""Tests for sc_core.shared.time."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from sc_core.shared import time as t


def test_utc_now_is_aware_utc() -> None:
    now = t.utc_now()
    assert now.tzinfo is UTC


def test_ensure_aware_assumes_utc_by_default() -> None:
    naive = datetime(2026, 9, 8, 12, 0)  # noqa: DTZ001
    assert t.ensure_aware(naive) == datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def test_ensure_aware_with_local_assumption() -> None:
    naive = datetime(2026, 9, 8, 12, 0)  # noqa: DTZ001
    aware = t.ensure_aware(naive, assume=t.LIMA)
    assert t.to_utc(aware).hour == 17  # Lima is UTC-5 all year


def test_to_local_lima() -> None:
    utc = datetime(2026, 9, 8, 3, 30, tzinfo=UTC)
    local = t.to_local(utc)
    assert (local.day, local.hour, local.minute) == (7, 22, 30)


def test_local_today_crosses_midnight() -> None:
    assert t.local_today(now=datetime(2026, 9, 8, 3, 0, tzinfo=UTC)) == date(2026, 9, 7)
    assert t.local_today(tz=ZoneInfo("UTC"), now=datetime(2026, 9, 8, 3, 0, tzinfo=UTC)) == date(
        2026, 9, 8
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-08T10:00:00Z", datetime(2026, 9, 8, 10, tzinfo=UTC)),
        ("2026-09-08T10:00:00+00:00", datetime(2026, 9, 8, 10, tzinfo=UTC)),
        ("2026-09-08T05:00:00-05:00", datetime(2026, 9, 8, 10, tzinfo=UTC)),
        ("2026-09-08T10:00:00", datetime(2026, 9, 8, 10, tzinfo=UTC)),  # naive → assumed UTC
        ("2026-09-08 10:00:00", datetime(2026, 9, 8, 10, tzinfo=UTC)),  # Odoo style
    ],
)
def test_parse_iso(text: str, expected: datetime) -> None:
    assert t.parse_iso(text) == expected


def test_iso_utc_canonical() -> None:
    lima = datetime(2026, 9, 8, 5, 0, 0, 123456, tzinfo=t.LIMA)
    assert t.iso_utc(lima) == "2026-09-08T10:00:00Z"


@pytest.mark.parametrize(
    ("start", "days", "expected"),
    [
        (date(2026, 9, 7), 0, date(2026, 9, 7)),  # Monday
        (date(2026, 9, 7), 1, date(2026, 9, 8)),
        (date(2026, 9, 11), 1, date(2026, 9, 14)),  # Friday +1 → Monday
        (date(2026, 9, 12), 1, date(2026, 9, 14)),  # Saturday +1 → Monday
        (date(2026, 9, 7), 5, date(2026, 9, 14)),
        (date(2026, 9, 14), -1, date(2026, 9, 11)),  # Monday −1 → Friday
    ],
)
def test_add_business_days(start: date, days: int, expected: date) -> None:
    assert t.add_business_days(start, days) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 9, 7), date(2026, 9, 7), 0),
        (date(2026, 9, 11), date(2026, 9, 14), 1),  # Fri → Mon
        (date(2026, 9, 7), date(2026, 9, 14), 5),
        (date(2026, 9, 12), date(2026, 9, 13), 0),  # Sat → Sun
        (date(2026, 9, 14), date(2026, 9, 7), -5),
    ],
)
def test_business_days_between(start: date, end: date, expected: int) -> None:
    assert t.business_days_between(start, end) == expected
