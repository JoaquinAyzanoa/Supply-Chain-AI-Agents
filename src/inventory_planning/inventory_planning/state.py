"""The planner's LangGraph state: plain dicts so the checkpointer serialises them.

The dataset, forecasts, lines and proposal are stored as JSON dicts; nodes
rebuild the typed models when they need them. Nothing sensitive lives here
(numbers, product refs, supplier names).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sc_core.graph import BaseAgentState


class PlanningState(BaseAgentState, total=False):
    task: dict[str, Any]
    dataset: dict[str, Any] | None
    forecasts: dict[str, dict[str, Any]] | None  # product id (str) -> forecast summary
    params: dict[str, dict[str, Any]] | None  # product id (str) -> ProductParams
    lines: list[dict[str, Any]] | None
    proposal: dict[str, Any] | None
    applied: dict[str, Any] | None
    outcome: dict[str, Any] | None


Node = Callable[[PlanningState], Awaitable[dict[str, Any]]]
