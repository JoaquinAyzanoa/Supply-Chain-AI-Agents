"""An agent finished a run the director did not see the end of.

Approval callbacks go from Odoo straight to the agent, which resumes and
finishes on its own. The agent then tells the director how it ended, so the
case leaves ``awaiting_approval``.
"""

from __future__ import annotations

from director.router import Dispatch, Route
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.events import AgentRunFinished, BaseEvent, RfqDrafted


def on_run_finished(event: BaseEvent) -> Route:
    assert isinstance(event, AgentRunFinished)
    return Route(
        case_kind="inbound",
        po_name=event.po_name,
        note=f"{event.agent} finished {event.task_kind} run {event.run_id}: {event.status}",
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
