"""Keys every agent state carries.

LangGraph states are ``TypedDict``s so nodes return partial updates. Agents
extend ``BaseAgentState`` with their own keys. ``pending_approvals`` holds
what was created in Odoo and ``approvals`` the decisions received; both are
append-only across the run. ``run_config`` builds the ``thread_id``
configuration LangGraph needs to find the checkpoint of a case.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig


def append(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    return [*(left or []), *(right or [])]


class BaseAgentState(TypedDict, total=False):
    case_id: str
    trace_id: str | None
    run_id: str
    model: str
    pending_approvals: Annotated[list[dict[str, Any]], append]
    approvals: Annotated[list[dict[str, Any]], append]


def run_config(case_id: str) -> RunnableConfig:
    """One LangGraph thread per business case, so a resume finds the paused graph."""
    return {"configurable": {"thread_id": case_id}}
