"""A shipping notice: extracted by the model, proposed as a date change, applied after approval."""

from __future__ import annotations

from datetime import date
from typing import Any

from logistics.carriers import FakeCarrierTracking, TrackingInfo
from logistics.testing import FakeLogisticsPorts
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import LogisticsTask, ShipmentInfo
from tests.unit.graph.toy import FakeApprovalPorts

NOTICE = ShipmentInfo(
    carrier="DHL",
    tracking_number="1234567890",
    ship_date=date(2026, 9, 30),
    eta_date=date(2026, 10, 5),
    eta_date_raw="llega el 5 de octubre",
    confidence=0.9,
)


def _task(**over: Any) -> LogisticsTask:
    base: dict[str, Any] = {
        "kind": "track_shipment",
        "case_id": "c_ship1",
        "po_name": "P00015",
        "graph_message_id": "notice1",
    }
    return LogisticsTask(**{**base, **over})


async def test_notice_proposes_the_arrival_then_applies_it_everywhere(
    make_agent: Any,
    ports: FakeLogisticsPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    chat.responses.append(NOTICE)
    agent = make_agent()
    paused = await agent.run(_task())
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101
    assert paused.shipment is not None and paused.shipment.tracking_number == "1234567890"
    assert paused.proposal is not None and len(paused.proposal.changes) == 2
    assert {c.source for c in paused.proposal.changes} == {"tracking"}
    assert all(c.after == "2026-10-05" and not c.needs_review for c in paused.proposal.changes)
    [created] = approval_ports.created
    assert created["kind"] == "po_change" and created["po_id"] == 7
    assert created["payload"]["classification"] == {"kind": "shipping_notice"}
    assert created["payload"]["shipment"]["carrier"] == "DHL"
    assert "DHL" in created["summary"] and "1234567890" in created["summary"]
    done = await agent.resume(
        "c_ship1", {"approval_id": 101, "status": "approved", "resolved_by": "Ana"}
    )
    assert done.status == "applied" and "2 line(s)" in done.outcome.summary
    assert [d["line_id"] for d in ports.line_dates] == [31, 32]
    assert all(
        d["source"] == "tracking" and d["date"] == date(2026, 10, 5) for d in ports.line_dates
    )
    assert ports.picking_dates == [
        {"picking_id": 42, "date": date(2026, 10, 5), "run_id": paused.run_id}
    ]
    assert ports.eta_meta == [{"po_id": 7, "confidence": 0.9, "source": "tracking"}]
    [(po_id, note)] = ports.base.notes
    assert po_id == 7 and "DHL" in note and "1234567890" in note and "Ana" in note
    assert ports.runs[paused.run_id]["status"] == "applied"
    # the notice's text is cleared once the run ends
    assert (await agent.snapshot("c_ship1")).get("inbound_text") is None


async def test_carrier_refines_the_arrival_date(
    make_agent: Any, chat: ScriptedChatClient, tracker: FakeCarrierTracking
) -> None:
    tracker.known["1234567890"] = TrackingInfo(
        carrier="DHL", tracking_number="1234567890", status="in transit", eta_date=date(2026, 10, 7)
    )
    chat.responses.append(NOTICE.model_copy(update={"confidence": 0.6}))
    paused = await make_agent().run(_task())
    assert tracker.asked == [("DHL", "1234567890")]
    assert paused.shipment is not None and paused.shipment.eta_date == date(2026, 10, 7)
    assert paused.shipment.confidence == 0.9  # the carrier's word beats a shaky reading
    assert paused.proposal is not None and paused.proposal.changes[0].after == "2026-10-07"


async def test_notice_without_a_date_or_with_the_same_date_needs_nothing(
    make_agent: Any, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    chat.responses.append(NOTICE.model_copy(update={"eta_date": None, "eta_date_raw": None}))
    none = await make_agent().run(_task(case_id="c_ship2"))
    assert none.status == "no_action" and "no arrival date" in none.outcome.summary
    chat.responses.append(NOTICE.model_copy(update={"eta_date": date(2026, 10, 1)}))
    same = await make_agent().run(_task(case_id="c_ship3"))
    assert same.status == "no_action" and "confirms the planned arrival" in same.outcome.summary
    assert approval_ports.created == []


async def test_low_confidence_dates_are_shown_but_not_written(
    make_agent: Any, ports: FakeLogisticsPorts, chat: ScriptedChatClient
) -> None:
    chat.responses.append(NOTICE.model_copy(update={"confidence": 0.4}))
    agent = make_agent()
    paused = await agent.run(_task(case_id="c_ship4"))
    assert paused.proposal is not None and all(c.needs_review for c in paused.proposal.changes)
    done = await agent.resume(
        "c_ship4", {"approval_id": 101, "status": "approved", "resolved_by": "Ana"}
    )
    assert done.status == "applied" and ports.line_dates == [] and ports.picking_dates == []
    assert "0 line(s)" in done.outcome.summary and "2" in done.outcome.summary


async def test_rejected_notice_leaves_a_note(
    make_agent: Any, ports: FakeLogisticsPorts, chat: ScriptedChatClient
) -> None:
    chat.responses.append(NOTICE)
    agent = make_agent()
    await agent.run(_task(case_id="c_ship5"))
    done = await agent.resume(
        "c_ship5",
        {"approval_id": 101, "status": "rejected", "resolved_by": "Ana", "reason": "wrong order"},
    )
    assert done.status == "rejected" and "wrong order" in done.outcome.summary
    assert ports.line_dates == [] and any("wrong order" in n for _, n in ports.base.notes)
