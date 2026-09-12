"""Trace metadata across an A2A hop (Langfuse disabled: ids are None, context still binds)."""

from __future__ import annotations

from sc_core.a2a.trace import propagate_from_metadata, trace_metadata
from sc_core.infra import context, tracing
from sc_core.infra.settings import Settings


def test_metadata_without_active_trace_has_only_the_case() -> None:
    tracing.configure_tracing(Settings(_env_file=None, service_name="t", environment="test"))
    assert trace_metadata("case_1") == {"case_id": "case_1"}


def test_propagate_continues_or_starts() -> None:
    tracing.configure_tracing(Settings(_env_file=None, service_name="t", environment="test"))
    with propagate_from_metadata(
        {"trace_id": "a" * 32, "case_id": "case_x"}, "agent.task", fallback_case_id="ctx"
    ):
        assert context.case_id.get() == "case_x" and context.trace_id.get() == "a" * 32
    with propagate_from_metadata(None, "agent.task", fallback_case_id="ctx_7"):
        assert context.case_id.get() == "ctx_7"
    assert context.case_id.get() is None
