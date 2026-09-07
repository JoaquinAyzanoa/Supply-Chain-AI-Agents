"""Money as an exact amount plus a currency.

Floats are never used for prices. ``Decimal`` arithmetic with explicit
rounding (half up, the accounting convention) keeps totals reproducible
across the planner, the invoice matcher and what Odoo stores. Mixing
currencies is an error, not an implicit conversion: the system flags
currency mismatches for a human instead of guessing a rate.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import Field, field_validator, model_validator

from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ValidationFailed

_CURRENCY = re.compile(r"^[A-Z]{3}$")

# Minor-unit digits for currencies the business actually uses; ISO 4217 default is 2.
_DECIMALS: dict[str, int] = {"JPY": 0, "CLP": 0, "KRW": 0}


class CurrencyMismatch(ValidationFailed):
    code = "currency_mismatch"


def decimals_for(currency: str) -> int:
    return _DECIMALS.get(currency, 2)


class Money(StrictModel):
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("currency")
    @classmethod
    def _iso_code(cls, value: str) -> str:
        if not _CURRENCY.match(value):
            raise ValueError(f"currency must be a 3-letter ISO code, got {value!r}")
        return value

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        # A float has already lost precision; force callers to pass str/int/Decimal.
        if isinstance(value, float):
            raise ValueError("amount must not be a float; pass a str, int or Decimal")
        return value

    @model_validator(mode="after")
    def _quantize(self) -> Money:
        places = Decimal(1).scaleb(-decimals_for(self.currency))
        quantized = self.amount.quantize(places, rounding=ROUND_HALF_UP)
        if quantized != self.amount:
            object.__setattr__(self, "amount", quantized)
        return self

    # --- construction -------------------------------------------------------

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: str) -> Money:
        return cls(amount=Decimal(str(amount)), currency=currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls.of(0, currency)

    # --- arithmetic ---------------------------------------------------------

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} with {other.currency}",
                details={"left": self.currency, "right": other.currency},
            )

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount=-self.amount, currency=self.currency)

    def times(self, factor: int | Decimal | str) -> Money:
        """Multiply by a quantity or rate; result is rounded to the currency's minor unit."""
        return Money(amount=self.amount * Decimal(str(factor)), currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    def pct_diff_from(self, reference: Money) -> Decimal:
        """Relative difference ``(self - reference) / reference`` as a percentage.

        Used by invoice matching (billed vs ordered price). Undefined for a
        zero reference; raises ``ValidationFailed`` in that case.
        """
        self._check(reference)
        if reference.amount == 0:
            raise ValidationFailed("percentage difference from a zero amount is undefined")
        return ((self.amount - reference.amount) / reference.amount * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def __str__(self) -> str:
        places = decimals_for(self.currency)
        return f"{self.amount:,.{places}f} {self.currency}"


def sum_money(items: list[Money], currency: str | None = None) -> Money:
    """Sum a list; ``currency`` is required when the list may be empty."""
    if not items:
        if currency is None:
            raise ValidationFailed("cannot sum an empty list without a currency")
        return Money.zero(currency)
    total = items[0]
    for item in items[1:]:
        total = total + item
    if currency is not None and total.currency != currency:
        raise CurrencyMismatch(
            f"expected {currency}, got {total.currency}",
            details={"expected": currency, "actual": total.currency},
        )
    return total
