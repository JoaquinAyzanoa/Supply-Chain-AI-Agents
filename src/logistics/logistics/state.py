"""The agent's LangGraph state: plain dicts and scalars, sensitive keys cleared at the end."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sc_core.graph import BaseAgentState


class LogisticsState(BaseAgentState, total=False):
    task: dict[str, Any]
    po_context: dict[str, Any] | None
    receipt: dict[str, Any] | None
    inbound_meta: dict[str, Any] | None
    inbound_text: str | None
    attachments_text: list[str] | None
    shipment: dict[str, Any] | None
    proposal: dict[str, Any] | None
    reconciliation: dict[str, Any] | None
    outbound: dict[str, Any] | None
    outbound_html: str | None
    sent: dict[str, Any] | None
    outcome: dict[str, Any] | None


Node = Callable[[LogisticsState], Awaitable[dict[str, Any]]]
