"""The agent's LangGraph state: plain dicts and scalars."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sc_core.graph import BaseAgentState


class SourcingState(BaseAgentState, total=False):
    task: dict[str, Any]
    order: dict[str, Any] | None  # OrderRef of the task's po_name
    basket: list[dict[str, Any]] | None
    options: list[dict[str, Any]] | None  # SupplierOption per candidate
    round: dict[str, Any] | None  # the Round the run works on
    mode: str | None  # award: "round" (RFQs exist) | "direct" (an RFQ is created on approval)
    comparison: dict[str, Any] | None  # QuoteComparison
    invited: list[dict[str, Any]] | None  # InvitedRfq
    offer: dict[str, Any] | None  # CounterOffer
    negotiation_id: int | None
    awarded_po_name: str | None
    outcome: dict[str, Any] | None
    escalation_approval_id: int | None


Node = Callable[[SourcingState], Awaitable[dict[str, Any]]]
