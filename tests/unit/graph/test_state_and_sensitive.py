"""State reducers and sensitive-key clearing."""

from __future__ import annotations

from sc_core.graph import SENSITIVE_KEYS, clear_sensitive, cleared, run_config
from sc_core.graph.state import append


def test_append_reducer_and_run_config() -> None:
    assert (
        append(None, [1]) == [1] and append([1], [2, 3]) == [1, 2, 3] and append([1], None) == [1]
    )
    assert run_config("case_9") == {"configurable": {"thread_id": "case_9"}}


def test_clear_sensitive_defaults_and_custom_keys() -> None:
    update = clear_sensitive()
    assert set(update) == set(SENSITIVE_KEYS) and all(v is None for v in update.values())
    assert clear_sensitive(["draft"]) == {"draft": None}
    assert cleared({"inbound_text": None, "attachments_text": []})
    assert not cleared({"inbound_text": "hola"})
