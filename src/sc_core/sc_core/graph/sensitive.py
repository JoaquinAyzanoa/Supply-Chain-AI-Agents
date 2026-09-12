"""Keep email text out of persisted state.

Nodes fetch the inbound body from Graph into the state for the duration of
a run. Every terminal node returns ``clear_sensitive()`` so the checkpoint
that survives the run holds identifiers only. ``cleared`` is the assertion
tests use on a checkpoint.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

SENSITIVE_KEYS: tuple[str, ...] = ("inbound_text", "attachments_text", "outbound_html")


def clear_sensitive(keys: Iterable[str] = SENSITIVE_KEYS) -> dict[str, Any]:
    """The state update that blanks ``keys``; merge it into a node's return value."""
    return dict.fromkeys(keys)


def cleared(state: Mapping[str, Any], keys: Iterable[str] = SENSITIVE_KEYS) -> bool:
    return all(not state.get(key) for key in keys)
