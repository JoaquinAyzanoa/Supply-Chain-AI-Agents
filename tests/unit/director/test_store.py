"""Case store rules on the in-memory implementation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from director.store import Case, CaseStore, MemoryCaseStore, matches

from .store_suite import CHECKS


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
async def test_memory_store(check: Callable[[CaseStore], Awaitable[None]]) -> None:
    store = MemoryCaseStore()
    assert isinstance(store, CaseStore)
    await check(store)


def _case(**overrides: object) -> Case:
    base: dict[str, object] = {
        "case_id": "case_1",
        "kind": "rfq",
        "status": "open",
        "po_name": "P00015",
        "conversation_id": "conv",
    }
    return Case(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("case", "po_name", "conversation_id", "expected"),
    [
        (_case(), "P00015", "conv", True),
        (_case(), "P00015", None, True),
        (_case(conversation_id=None), "P00015", "conv", True),
        (_case(), "P00015", "other", False),
        (_case(), "P00016", "conv", False),
        (_case(), None, "conv", False),
        (_case(status="done"), "P00015", "conv", False),
        (_case(status="failed"), "P00015", "conv", False),
        (_case(status="awaiting_approval"), "P00015", "conv", True),
        (_case(status="escalated"), "P00015", "conv", True),
    ],
)
def test_matches(
    case: Case, po_name: str | None, conversation_id: str | None, expected: bool
) -> None:
    assert matches(case, po_name=po_name, conversation_id=conversation_id) is expected
