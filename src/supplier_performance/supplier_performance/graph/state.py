"""The agent's LangGraph state: plain dicts and scalars."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sc_core.graph import BaseAgentState


class PerformanceState(BaseAgentState, total=False):
    task: dict[str, Any]
    period: dict[str, Any] | None  # start, end
    histories: list[dict[str, Any]] | None
    previous: dict[str, Any] | None  # partner_id (as str) -> SupplierScore
    scores: list[dict[str, Any]] | None
    applied: dict[str, Any] | None
    outcome: dict[str, Any] | None


Node = Callable[[PerformanceState], Awaitable[dict[str, Any]]]
