"""Helpers shared by the nodes."""

from __future__ import annotations

from typing import Any

from sc_core.graph import clear_sensitive
from sc_core.schema.a2a import Outcome, OutcomeStatus, SupplierCommsTask
from supplier_comms.models import PoContext


def task_of(state: dict[str, Any]) -> SupplierCommsTask:
    return SupplierCommsTask.model_validate(state["task"])


def context_of(state: dict[str, Any]) -> PoContext:
    ctx = state.get("po_context")
    if not ctx:
        raise RuntimeError("po_context missing; load_context must run first")
    return PoContext.model_validate(ctx)


def finish(status: OutcomeStatus, summary: str, **extra: Any) -> dict[str, Any]:
    """Terminal state update: the outcome plus the sensitive keys cleared."""
    return {
        "outcome": Outcome(status=status, summary=summary[:500]).model_dump(mode="json"),
        **clear_sensitive(),
        **extra,
    }


def fail(summary: str) -> dict[str, Any]:
    return finish("failed", summary)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
