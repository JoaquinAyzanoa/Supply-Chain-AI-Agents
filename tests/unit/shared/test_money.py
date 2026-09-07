"""Tests for sc_core.shared.money."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from sc_core.shared.errors import ValidationFailed
from sc_core.shared.money import CurrencyMismatch, Money, sum_money


def test_construction_and_rounding() -> None:
    m = Money.of("10.005", "pen")
    assert m.currency == "PEN"
    assert m.amount == Decimal("10.01")  # half up
    assert Money.of("1234.5", "JPY").amount == Decimal("1235")  # zero-decimal currency


def test_float_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Money(amount=0.1, currency="PEN")  # type: ignore[arg-type]


@pytest.mark.parametrize("code", ["PE", "PENN", "12A", ""])
def test_invalid_currency(code: str) -> None:
    with pytest.raises(ValidationError):
        Money.of("1", code)


def test_arithmetic_same_currency() -> None:
    a, b = Money.of("10.50", "USD"), Money.of("0.75", "USD")
    assert (a + b).amount == Decimal("11.25")
    assert (a - b).amount == Decimal("9.75")
    assert (-a).amount == Decimal("-10.50")
    assert a.times(3).amount == Decimal("31.50")
    assert a.times("0.333").amount == Decimal("3.50")  # 3.4965 → 3.50
    assert a > b and b < a and a >= a and b <= b


def test_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatch) as exc:
        Money.of("1", "USD") + Money.of("1", "PEN")
    assert exc.value.details == {"left": "USD", "right": "PEN"}
    with pytest.raises(CurrencyMismatch):
        assert Money.of("1", "USD") < Money.of("1", "PEN")


def test_pct_diff() -> None:
    ordered, billed = Money.of("100", "USD"), Money.of("103", "USD")
    assert billed.pct_diff_from(ordered) == Decimal("3.00")
    assert ordered.pct_diff_from(billed) == Decimal("-2.91")
    with pytest.raises(ValidationFailed):
        billed.pct_diff_from(Money.zero("USD"))


def test_sum_money() -> None:
    items = [Money.of("1.10", "PEN"), Money.of("2.20", "PEN")]
    assert sum_money(items).amount == Decimal("3.30")
    assert sum_money([], "PEN") == Money.zero("PEN")
    with pytest.raises(ValidationFailed):
        sum_money([])
    with pytest.raises(CurrencyMismatch):
        sum_money(items, "USD")


def test_str_and_json() -> None:
    m = Money.of("1234567.891", "PEN")
    assert str(m) == "1,234,567.89 PEN"
    assert m.model_dump_json() == '{"amount":"1234567.89","currency":"PEN"}'
    assert Money.model_validate_json(m.model_dump_json()) == m
