"""The agent's LangGraph state.

Everything is a plain dict or scalar so the checkpointer serialises it
without surprises; nodes rebuild the typed models when they need them.
``inbound_text``, ``attachments_text`` and ``outbound_html`` are the
sensitive keys cleared by every terminal node.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sc_core.graph import BaseAgentState


class SupplierCommsState(BaseAgentState, total=False):
    task: dict[str, Any]
    po_context: dict[str, Any] | None
    inbound_text: str | None
    attachments_text: list[str] | None
    classification: dict[str, Any] | None
    extracted: dict[str, Any] | None
    proposal: dict[str, Any] | None
    outbound: dict[str, Any] | None  # OutboundDraft without the body, plus Graph ids once drafted
    outbound_html: str | None
    sent: dict[str, Any] | None  # sent_message_id, web_link
    tool_exchange: list[dict[str, Any]] | None
    outcome: dict[str, Any] | None
    inbound_meta: dict[str, Any] | None  # sender address, subject token, Graph ids (no text)
    resolution: dict[str, Any] | None  # resolve_unlinked: the model's pick and reason
    chosen_po_name: str | None
    escalation_approval_id: int | None


# A graph node: takes the state, returns a partial update.
Node = Callable[[SupplierCommsState], Awaitable[dict[str, Any]]]
