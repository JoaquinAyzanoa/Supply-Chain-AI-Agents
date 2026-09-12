"""An agent finished a run the director did not see the end of.

Approval callbacks go from Odoo straight to the agent, which resumes and
finishes on its own. The agent then tells the director how it ended, so the
case leaves ``awaiting_approval``.
"""

from __future__ import annotations

from director.router import Route
from sc_core.schema.events import AgentRunFinished, BaseEvent


def on_run_finished(event: BaseEvent) -> Route:
    assert isinstance(event, AgentRunFinished)
    return Route(
        case_kind="inbound",
        po_name=event.po_name,
        note=f"{event.agent} finished {event.task_kind} run {event.run_id}: {event.status}",
    )
