"""Tests for sc_core.shared.idempotency."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sc_core.shared.idempotency import deterministic_id, make_key, new_id


def test_make_key_is_stable_and_order_sensitive() -> None:
    a = make_key("rfq", "plan_1", 42)
    assert a == make_key("rfq", "plan_1", 42)
    assert len(a) == 64
    assert a != make_key("rfq", 42, "plan_1")
    assert a != make_key("other", "plan_1", 42)


def test_make_key_distinguishes_types_and_none() -> None:
    assert make_key("k", 1) != make_key("k", "1")
    assert make_key("k", None) != make_key("k", "")
    assert make_key("k") != make_key("k", None)


def test_make_key_dict_order_irrelevant() -> None:
    assert make_key("k", {"a": 1, "b": 2}) == make_key("k", {"b": 2, "a": 1})


def test_make_key_handles_decimal_and_datetime() -> None:
    key = make_key("k", Decimal("1.10"), datetime(2026, 9, 8, tzinfo=UTC))
    assert key == make_key("k", Decimal("1.10"), datetime(2026, 9, 8, tzinfo=UTC))


def test_make_key_requires_namespace() -> None:
    with pytest.raises(ValueError):
        make_key("")


def test_deterministic_id() -> None:
    cid = deterministic_id("mail", "AAMkAGI2")
    assert cid == deterministic_id("mail", "AAMkAGI2")
    assert cid.startswith("mail_") and len(cid) == len("mail_") + 16
    assert deterministic_id("mail", "AAMkAGI2", length=32) != cid
    with pytest.raises(ValueError):
        deterministic_id("bad prefix", "x")
    with pytest.raises(ValueError):
        deterministic_id("mail", "x", length=4)


def test_new_id_is_unique_and_prefixed() -> None:
    a, b = new_id("evt"), new_id("evt")
    assert a != b
    assert a.startswith("evt_") and len(a) == 4 + 32
    with pytest.raises(ValueError):
        new_id("")
