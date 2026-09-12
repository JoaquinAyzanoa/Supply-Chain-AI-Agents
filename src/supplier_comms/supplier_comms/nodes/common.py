"""Helpers shared by the nodes."""

from __future__ import annotations

import re
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


_TABLE_STYLES = {
    "table": "border-collapse:collapse;border:1px solid #999;margin:8px 0",
    "th": "border:1px solid #999;padding:4px 8px;text-align:left;background:#f2f2f2",
    "td": "border:1px solid #999;padding:4px 8px",
}
_TAG = re.compile(r"<(table|th|td)(\s[^>]*)?>", re.IGNORECASE)


def style_tables(html: str) -> str:
    """Give the model's bare tables borders and padding, inline (mail clients drop
    stylesheets). Tags that already carry a ``style`` attribute are left alone."""

    def add(match: re.Match[str]) -> str:
        tag, attrs = match.group(1).lower(), match.group(2) or ""
        if "style=" in attrs.lower():
            return match.group(0)
        return f'<{tag}{attrs} style="{_TABLE_STYLES[tag]}">'

    return _TAG.sub(add, html)
