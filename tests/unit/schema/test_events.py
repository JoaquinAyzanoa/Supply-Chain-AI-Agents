"""Tests for sc_core.schema.events."""

from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from sc_core.schema.events import BaseEvent


class PingEvent(BaseEvent):
    type: Literal["ping"] = "ping"
    payload: str


class PongEvent(BaseEvent):
    type: Literal["pong"] = "pong"


def test_defaults_are_filled() -> None:
    evt = PingEvent(source="mail_sync", case_id="case_1", payload="x")
    assert evt.event_id.startswith("evt_")
    assert evt.occurred_at.tzinfo is not None
    assert evt.occurred_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert evt.schema_version == 1
    assert evt.trace_id is None


def test_required_fields() -> None:
    with pytest.raises(ValidationError):
        PingEvent(source="", case_id="c", payload="x")
    with pytest.raises(ValidationError):
        PingEvent(source="s", case_id="", payload="x")


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        PingEvent(source="s", case_id="c", payload="x", occurred_at=datetime(2026, 1, 1))  # noqa: DTZ001


def test_round_trip_json_and_discriminated_union() -> None:
    adapter: TypeAdapter[PingEvent | PongEvent] = TypeAdapter(PingEvent | PongEvent)
    original = PingEvent(
        source="s", case_id="c", payload="hola", occurred_at=datetime(2026, 9, 8, tzinfo=UTC)
    )
    parsed = adapter.validate_json(original.model_dump_json())
    assert parsed == original
    assert isinstance(
        adapter.validate_json('{"type":"pong","source":"s","case_id":"c"}'), PongEvent
    )


def test_with_trace_returns_copy() -> None:
    evt = PongEvent(source="s", case_id="c")
    traced = evt.with_trace("trace-1")
    assert traced.trace_id == "trace-1"
    assert evt.trace_id is None
    assert traced.event_id == evt.event_id
