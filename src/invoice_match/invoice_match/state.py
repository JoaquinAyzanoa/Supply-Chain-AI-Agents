"""The agent's LangGraph state: plain dicts and scalars, sensitive keys cleared at the end."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sc_core.graph import BaseAgentState


class InvoiceMatchState(BaseAgentState, total=False):
    task: dict[str, Any]
    po_context: dict[str, Any] | None
    bill: dict[str, Any] | None
    candidates: list[dict[str, Any]] | None
    supplier_id: int | None
    inbound_meta: dict[str, Any] | None
    inbound_text: str | None
    attachments_text: list[str] | None
    invoice: dict[str, Any] | None
    match: dict[str, Any] | None
    created_bill: dict[str, Any] | None
    outcome: dict[str, Any] | None


Node = Callable[[InvoiceMatchState], Awaitable[dict[str, Any]]]
