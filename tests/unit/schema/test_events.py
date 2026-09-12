"""Typed events: discriminated parsing and deterministic ids."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sc_core.schema.events import (
    InboundMailLinked,
    InboundMailUnlinked,
    ScheduledTick,
    event_id_for,
    parse_event,
)
from sc_core.shared.time import utc_now


def test_parse_each_type_roundtrip() -> None:
    linked = InboundMailLinked(
        source="mail_sync",
        case_id="case_1",
        po_id=7,
        po_name="P00015",
        graph_message_id="AAMk1",
        confidence="exact",
        rule="header",
    )
    unlinked = InboundMailUnlinked(
        source="mail_sync", case_id="case_2", graph_message_id="AAMk2", open_po_names=["P00016"]
    )
    tick = ScheduledTick(
        source="scheduler",
        case_id="run_1",
        job_id="mail_sync",
        run_id="run_1",
        scheduled_at=utc_now(),
    )
    for event in (linked, unlinked, tick):
        parsed = parse_event(event.model_dump_json())
        assert parsed == event and type(parsed) is type(event)
        assert parse_event(event.model_dump(mode="json")) == event


def test_unknown_type_and_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_event({"type": "nope", "source": "x", "case_id": "c"})
    with pytest.raises(ValidationError):
        InboundMailUnlinked(  # type: ignore[call-arg]
            source="s", case_id="c", graph_message_id="m", subject="never"
        )


def test_event_id_is_deterministic_per_type_and_message() -> None:
    a = event_id_for("inbound_mail.linked", "AAMk1")
    assert a == event_id_for("inbound_mail.linked", "AAMk1") and a.startswith("evt_")
    assert a != event_id_for("inbound_mail.unlinked", "AAMk1")
    assert a != event_id_for("inbound_mail.linked", "AAMk2")
