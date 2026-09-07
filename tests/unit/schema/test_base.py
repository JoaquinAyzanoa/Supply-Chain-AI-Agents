"""Tests for sc_core.schema.base."""

import pytest
from pydantic import ValidationError

from sc_core.schema.base import MutableModel, StrictModel


class Line(StrictModel):
    product: str
    qty: int = 1


class Draft(MutableModel):
    title: str


def test_strict_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        Line(product="A", quantity=2)  # type: ignore[call-arg]
    assert "quantity" in str(exc.value)


def test_strict_is_frozen() -> None:
    line = Line(product="A")
    with pytest.raises(ValidationError):
        line.qty = 5  # type: ignore[misc]


def test_strict_strips_strings() -> None:
    assert Line(product="  A  ").product == "A"


def test_model_copy_is_the_way_to_change() -> None:
    line = Line(product="A")
    other = line.model_copy(update={"qty": 3})
    assert (line.qty, other.qty) == (1, 3)


def test_mutable_validates_on_assignment() -> None:
    draft = Draft(title="x")
    draft.title = "y"
    with pytest.raises(ValidationError):
        draft.title = 3  # type: ignore[assignment]
    with pytest.raises(ValidationError):
        Draft(title="x", extra=1)  # type: ignore[call-arg]
