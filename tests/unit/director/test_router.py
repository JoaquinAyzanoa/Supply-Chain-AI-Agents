"""Every event type has a deterministic route; handlers never call a model."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from director.router import ROUTES, Route, UnroutableEvent, route
from sc_core.schema import events as ev
from sc_core.schema.events import EVENT_TYPES, BaseEvent


def test_every_event_type_has_a_route() -> None:
    assert set(ROUTES) == set(EVENT_TYPES)
    # the discriminated union knows every listed type (so /events can parse it)
    parsed_types = {
        member
        for member in ev.AnyEvent.__origin__.__args__  # type: ignore[attr-defined]
    }
    assert parsed_types == set(EVENT_TYPES)


def test_unknown_event_type_is_unroutable() -> None:
    class Custom(BaseEvent):
        pass

    with pytest.raises(UnroutableEvent, match="custom"):
        route(Custom(type="custom", source="x", case_id="c"))


def _linked(**overrides: object) -> ev.InboundMailLinked:
    base: dict[str, object] = {
        "source": "mail_sync",
        "case_id": "case_msg1",
        "po_id": 7,
        "po_name": "P00015",
        "graph_message_id": "AAMk1",
        "conversation_id": "conv1",
        "confidence": "exact",
        "rule": "header",
    }
    return ev.InboundMailLinked(**{**base, **overrides})  # type: ignore[arg-type]


def test_linked_mail_becomes_handle_inbound() -> None:
    decided = route(_linked())
    assert decided.case_kind == "inbound" and decided.po_name == "P00015"
    assert decided.conversation_id == "conv1" and decided.escalate is None
    [dispatch] = decided.dispatches
    assert dispatch.agent == "supplier_comms" and dispatch.thread_id == "case_msg1"
    assert dispatch.task.kind == "handle_inbound" and dispatch.task.graph_message_id == "AAMk1"
    assert dispatch.task.po_name == "P00015"


def test_unlinked_mail_with_candidates_becomes_resolve_task() -> None:
    event = ev.InboundMailUnlinked(
        source="mail_sync",
        case_id="case_msg2",
        graph_message_id="AAMk2",
        conversation_id="conv2",
        sender_address="v@x.com",
        partner_id=42,
        open_po_names=["P00015", "P00016"],
    )
    decided = route(event)
    assert decided.case_kind == "unlinked" and decided.po_name is None
    assert decided.partner_id == 42 and decided.conversation_id == "conv2"
    [dispatch] = decided.dispatches
    assert dispatch.task.kind == "resolve_unlinked"
    assert dispatch.task.candidate_po_names == ["P00015", "P00016"]
    assert dispatch.task.case_id == "case_msg2"


def test_unlinked_mail_from_unknown_sender_escalates_without_agent() -> None:
    event = ev.InboundMailUnlinked(
        source="mail_sync", case_id="case_msg3", graph_message_id="AAMk3", sender_address="a@b.c"
    )
    decided = route(event)
    assert decided.dispatches == [] and decided.escalate is not None
    assert "unknown sender" in decided.escalate


def test_po_confirmed_sends_the_order() -> None:
    event = ev.OdooPurchaseConfirmed(
        source="odoo",
        case_id="odoo_po_66_purchase",
        po_id=66,
        po_name="P00066",
        partner_id=9,
        date_planned=datetime(2026, 10, 1, tzinfo=UTC),
        amount_total=1234.5,
        currency="PEN",
        line_count=2,
    )
    decided = route(event)
    assert decided.case_kind == "eta" and decided.po_name == "P00066" and decided.partner_id == 9
    [dispatch] = decided.dispatches
    assert dispatch.task.kind == "send_po" and dispatch.task.po_name == "P00066"
    assert dispatch.thread_id == "odoo_po_66_purchase"


def test_receipt_is_recorded_and_orderpoint_goes_to_the_planner() -> None:
    receipt = route(
        ev.OdooReceiptValidated(
            source="odoo",
            case_id="odoo_picking_5",
            picking_id=5,
            picking_name="WH/IN/00005",
            po_id=66,
            po_name="P00066",
        )
    )
    assert receipt.case_kind == "receipt" and receipt.po_name == "P00066"
    assert receipt.dispatches == [] and receipt.note and "WH/IN/00005" in receipt.note
    orderpoint = route(
        ev.OdooOrderpointTriggered(
            source="odoo",
            case_id="odoo_orderpoint_3",
            orderpoint_id=3,
            product_id=11,
            product_code="CBEA-LHN",
            qty_to_order=12,
        )
    )
    assert orderpoint.case_kind == "planning" and orderpoint.po_name is None
    [dispatch] = orderpoint.dispatches
    assert dispatch.agent == "inventory_planning" and dispatch.task.kind == "review_product"
    assert dispatch.task.product_ids == [11] and dispatch.thread_id == "odoo_orderpoint_3"
    assert dispatch.task.context and "CBEA-LHN" in dispatch.task.context


def test_approval_resolved_is_mirrored() -> None:
    decided = route(
        ev.OdooApprovalResolved(
            source="odoo",
            case_id="odoo_approval_101",
            approval_id=101,
            kind="send_email",
            status="approved",
            thread_id="case_msg1",
            po_id=7,
            po_name="P00015",
            resolved_by="admin",
        )
    )
    assert decided.case_kind == "rfq" and decided.po_name == "P00015"
    assert (
        decided.dispatches == [] and decided.note == "approval 101 (send_email) approved by admin"
    )
    other = route(
        ev.OdooApprovalResolved(
            source="odoo", case_id="c", approval_id=1, kind="something_new", status="rejected"
        )
    )
    assert other.case_kind == "inbound" and other.note == "approval 1 (something_new) rejected"


def test_agent_run_finished_is_recorded() -> None:
    decided = route(
        ev.AgentRunFinished(
            source="supplier_comms",
            case_id="case_msg1",
            agent="supplier_comms",
            thread_id="case_msg1",
            run_id="run_1",
            task_kind="handle_inbound",
            status="applied",
            summary="ETA updated",
            po_name="P00015",
        )
    )
    assert decided.po_name == "P00015" and decided.dispatches == []
    assert decided.note == "supplier_comms finished handle_inbound run run_1: applied"


@pytest.mark.parametrize(
    ("job_id", "expected"),
    [
        ("po_followups", Route(case_kind="eta", job="po_followups")),
        ("inventory_planning", Route(case_kind="planning", job="inventory_planning")),
        ("supplier_performance", Route(case_kind="receipt", job="supplier_performance")),
    ],
)
def test_scheduler_tick_names_the_job(job_id: str, expected: Route) -> None:
    tick = ev.ScheduledTick(
        source="scheduler",
        case_id="run_1",
        job_id=job_id,
        run_id="run_1",
        scheduled_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
    )
    assert route(tick) == expected


def test_unknown_job_is_a_note_not_a_crash() -> None:
    tick = ev.ScheduledTick(
        source="scheduler",
        case_id="run_2",
        job_id="nope",
        run_id="run_2",
        scheduled_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
    )
    decided = route(tick)
    assert decided.job is None and decided.note == "unknown scheduler job 'nope'"
