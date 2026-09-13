"""Helpers shared by the nodes; the generic ones come from the supplier agent."""

from __future__ import annotations

from typing import Any

from invoice_match.models import BillView
from sc_core.schema.a2a import InvoiceData, InvoiceMatchTask
from supplier_comms.nodes.common import context_of, esc, fail, finish, style_tables

__all__ = [
    "bill_of",
    "context_of",
    "esc",
    "fail",
    "finish",
    "invoice_of",
    "style_tables",
    "task_of",
]


def task_of(state: dict[str, Any]) -> InvoiceMatchTask:
    return InvoiceMatchTask.model_validate(state["task"])


def invoice_of(state: dict[str, Any]) -> InvoiceData:
    data = state.get("invoice")
    if not data:
        raise RuntimeError("invoice missing; read_invoice must run first")
    return InvoiceData.model_validate(data)


def bill_of(state: dict[str, Any]) -> BillView | None:
    data = state.get("bill")
    return BillView.model_validate(data) if data else None
