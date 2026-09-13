"""Builders shared by the director tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sc_core.a2a import AgentReply
from sc_core.schema import events as ev
from sc_core.schema.a2a import (
    Classification,
    LogisticsResult,
    OutboundSummary,
    Outcome,
    OutcomeStatus,
    SupplierCommsResult,
)


def agent_reply(
    kind: str,
    thread_id: str,
    status: OutcomeStatus,
    summary: str = "done",
    *,
    approval_id: int | None = None,
    sent_message_id: str | None = None,
    po_name: str | None = "P00015",
) -> AgentReply:
    """A well-formed supplier_comms result as the A2A client would return it."""
    outbound = None
    if sent_message_id:
        outbound = OutboundSummary(
            kind="rfq", to=["v@x.com"], subject="s", sent_message_id=sent_message_id
        )
    result = SupplierCommsResult(
        kind=kind,  # type: ignore[arg-type]
        case_id=thread_id,
        run_id="run_1",
        outcome=Outcome(status=status, summary=summary, approval_id=approval_id),
        po_name=po_name,
        outbound=outbound,
    )
    a2a_status = "input_required" if status == "awaiting_approval" else "completed"
    return AgentReply(status=a2a_status, text=result.model_dump_json())


def logistics_reply(
    kind: str,
    thread_id: str,
    status: OutcomeStatus,
    summary: str = "done",
    *,
    approval_id: int | None = None,
    po_name: str | None = "P00015",
) -> AgentReply:
    """A well-formed logistics result as the A2A client would return it."""
    result = LogisticsResult(
        kind=kind,  # type: ignore[arg-type]
        case_id=thread_id,
        run_id="run_l1",
        outcome=Outcome(status=status, summary=summary, approval_id=approval_id),
        po_name=po_name,
    )
    a2a_status = "input_required" if status == "awaiting_approval" else "completed"
    return AgentReply(status=a2a_status, text=result.model_dump_json())


def classified_reply(
    kind: str, thread_id: str, classification: str, *, po_name: str = "P00015"
) -> AgentReply:
    """A supplier_comms ``handle_inbound`` result carrying the model's classification."""
    result = SupplierCommsResult(
        kind=kind,  # type: ignore[arg-type]
        case_id=thread_id,
        run_id="run_1",
        outcome=Outcome(status="no_action", summary=f"{classification}: nothing to change"),
        po_name=po_name,
        classification=Classification(kind=classification, confidence=0.9, reason="test"),  # type: ignore[arg-type]
    )
    return AgentReply(status="completed", text=result.model_dump_json())


def linked(**overrides: Any) -> ev.InboundMailLinked:
    base: dict[str, Any] = {
        "source": "mail_sync",
        "case_id": "case_msg1",
        "po_id": 7,
        "po_name": "P00015",
        "graph_message_id": "AAMk1",
        "conversation_id": "conv1",
        "confidence": "exact",
        "rule": "header",
    }
    return ev.InboundMailLinked(**{**base, **overrides})


def po_confirmed(**overrides: Any) -> ev.OdooPurchaseConfirmed:
    base: dict[str, Any] = {
        "source": "odoo",
        "case_id": "odoo_po_66_purchase",
        "po_id": 66,
        "po_name": "P00066",
        "partner_id": 9,
        "date_planned": datetime(2026, 10, 1, tzinfo=UTC),
    }
    return ev.OdooPurchaseConfirmed(**{**base, **overrides})


def tick(job_id: str = "po_followups", run_id: str = "run_1") -> ev.ScheduledTick:
    return ev.ScheduledTick(
        source="scheduler",
        case_id=run_id,
        job_id=job_id,
        run_id=run_id,
        scheduled_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
    )
