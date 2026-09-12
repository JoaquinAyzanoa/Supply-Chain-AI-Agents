"""Plumbing shared by every LangGraph agent.

- ``checkpointer``: LangGraph state persisted in the app database so a graph
  paused on a human approval resumes in any process.
- ``state``: the keys every agent state carries (case, trace, run, model,
  approvals).
- ``approval``: the interrupt node that creates an ``sc.approval`` in Odoo,
  schedules the reviewer's activity and pauses until the decision arrives.
- ``tools``: read-only tools the model may call, executed with a Langfuse
  span around each call and errors mapped to a result the model can read.
- ``sensitive``: clearing email text out of the state before a run ends.
"""

from sc_core.graph.approval import (
    ApprovalDecision,
    ApprovalGateway,
    ApprovalRequest,
    OdooApprovalPorts,
    decision_for,
)
from sc_core.graph.checkpointer import build_checkpointer, memory_checkpointer
from sc_core.graph.sensitive import SENSITIVE_KEYS, clear_sensitive, cleared
from sc_core.graph.state import BaseAgentState, run_config
from sc_core.graph.tools import Tool, ToolBox, ToolCall, ToolError, tool

__all__ = [
    "SENSITIVE_KEYS",
    "ApprovalDecision",
    "ApprovalGateway",
    "ApprovalRequest",
    "BaseAgentState",
    "OdooApprovalPorts",
    "Tool",
    "ToolBox",
    "ToolCall",
    "ToolError",
    "build_checkpointer",
    "clear_sensitive",
    "decision_for",
    "cleared",
    "memory_checkpointer",
    "run_config",
    "tool",
]
