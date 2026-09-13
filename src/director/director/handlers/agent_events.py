"""An agent finished a run the director did not see the end of.

Approval callbacks go from Odoo straight to the agent, which resumes and
finishes on its own. The agent then tells the director how it ended, so the
case leaves ``awaiting_approval``.
"""

from __future__ import annotations

from director.router import Dispatch, Route
from sc_core.schema.a2a import SourcingTask, SupplierCommsTask
from sc_core.schema.events import AgentRunFinished, BaseEvent, NeedsProposed, RfqDrafted


def on_run_finished(event: BaseEvent) -> Route:
    assert isinstance(event, AgentRunFinished)
    return Route(
        case_kind="inbound",
        po_name=event.po_name,
        note=f"{event.agent} finished {event.task_kind} run {event.run_id}: {event.status}",
    )


def on_needs_proposed(event: BaseEvent) -> Route:
    """The plan's buys become one quote round: the sourcing agent asks the suppliers who
    list each product and a person awards. No supplier is chosen by the planner."""
    assert isinstance(event, NeedsProposed)
    task = SourcingTask(
        kind="quote_round",
        case_id=f"round_plan_{event.run_id}",
        needs=list(event.needs),
        reason=f"daily plan {event.run_id}",
    )
    return Route(
        case_kind="sourcing",
        dispatches=[Dispatch(agent="sourcing", task=task)],
        snapshot={"planning_run_id": event.run_id, "needs": len(event.needs)},
    )


def on_rfq_drafted(event: BaseEvent) -> Route:
    """A draft RFQ from the planner becomes a ``send_rfq`` task for the supplier agent."""
    assert isinstance(event, RfqDrafted)
    task = SupplierCommsTask(kind="send_rfq", case_id=event.case_id, po_name=event.po_name)
    return Route(
        case_kind="rfq",
        po_name=event.po_name,
        partner_id=event.partner_id,
        dispatches=[Dispatch(agent="supplier_comms", task=task)],
        snapshot={"planning_run_id": event.run_id, "line_count": event.line_count},
    )
