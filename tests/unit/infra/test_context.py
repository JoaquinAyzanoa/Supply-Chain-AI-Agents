"""Tests for sc_core.infra.context."""

import asyncio

import pytest

from sc_core.infra import context


def test_bind_sets_and_restores() -> None:
    assert context.case_id.get() is None
    with context.bind(case_id="c1", run_id="r1"):
        assert context.case_id.get() == "c1"
        assert context.run_id.get() == "r1"
        with context.bind(case_id="c2"):
            assert context.case_id.get() == "c2"
            assert context.run_id.get() == "r1"
        assert context.case_id.get() == "c1"
    assert context.case_id.get() is None
    assert context.run_id.get() is None


def test_bind_restores_on_exception() -> None:
    with pytest.raises(RuntimeError):
        with context.bind(trace_id="t1"):
            raise RuntimeError("boom")
    assert context.trace_id.get() is None


def test_bind_rejects_unknown_names() -> None:
    with pytest.raises(KeyError, match="cas_id"):
        with context.bind(cas_id="typo"):
            pass


def test_snapshot_lists_every_variable() -> None:
    with context.bind(service_name="svc", case_id="c"):
        snap = context.snapshot()
    assert snap == {
        "service_name": "svc",
        "case_id": "c",
        "trace_id": None,
        "run_id": None,
        "request_id": None,
    }


async def test_context_is_isolated_between_tasks() -> None:
    """Concurrent tasks must not see each other's case id."""
    seen: dict[str, str | None] = {}

    async def work(name: str) -> None:
        with context.bind(case_id=name):
            await asyncio.sleep(0.01)
            seen[name] = context.case_id.get()

    await asyncio.gather(work("a"), work("b"))
    assert seen == {"a": "a", "b": "b"}
    assert context.case_id.get() is None
