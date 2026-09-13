"""Helpers shared by the nodes; the generic ones come from the supplier agent."""

from __future__ import annotations

from typing import Any

from logistics.models import ReceiptView
from sc_core.schema.a2a import LogisticsTask
from supplier_comms.nodes.common import context_of, esc, fail, finish, style_tables

__all__ = ["context_of", "esc", "fail", "finish", "receipt_of", "style_tables", "task_of"]


def task_of(state: dict[str, Any]) -> LogisticsTask:
    return LogisticsTask.model_validate(state["task"])


def receipt_of(state: dict[str, Any]) -> ReceiptView:
    receipt = state.get("receipt")
    if not receipt:
        raise RuntimeError("receipt missing; load_context must run first")
    return ReceiptView.model_validate(receipt)
