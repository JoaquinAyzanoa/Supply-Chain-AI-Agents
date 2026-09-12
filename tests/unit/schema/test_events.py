"""Typed events: discriminated parsing and deterministic ids."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sc_core.schema.events import (
    EVENT_TYPES,
    AgentRunFinished,
    InboundMailLinked,
    InboundMailUnlinked,
    OdooApprovalResolved,
    OdooOrderpointTriggered,
    OdooPurchaseConfirmed,
    OdooReceiptValidated,
    RfqDrafted,
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
    confirmed = OdooPurchaseConfirmed(
        source="odoo", case_id="odoo_po_1", po_id=1, po_name="P00001", partner_id=3
    )
    receipt = OdooReceiptValidated(
        source="odoo", case_id="odoo_pick_1", picking_id=1, picking_name="WH/IN/00001"
    )
    orderpoint = OdooOrderpointTriggered(
        source="odoo", case_id="odoo_op_1", orderpoint_id=1, product_id=2, qty_to_order=5
    )
    resolved = OdooApprovalResolved(
        source="odoo", case_id="odoo_appr_1", approval_id=1, kind="send_email", status="approved"
    )
    finished = AgentRunFinished(
        source="supplier_comms",
        case_id="case_1",
        agent="supplier_comms",
        thread_id="case_1",
        run_id="run_1",
        task_kind="send_rfq",
        status="sent",
        summary="RFQ sent",
    )
    drafted = RfqDrafted(
        source="inventory_planning",
        case_id="plan_1",
        po_id=70,
        po_name="P00070",
        partner_id=20,
        run_id="run_1",
    )
    events = (linked, unlinked, tick, confirmed, receipt, orderpoint, resolved, finished, drafted)
    assert {type(e) for e in events} == set(EVENT_TYPES)
    for event in events:
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
