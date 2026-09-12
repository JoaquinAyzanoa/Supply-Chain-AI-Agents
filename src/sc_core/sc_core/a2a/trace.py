"""Carry the Langfuse trace and the case across an A2A hop.

The caller puts ``trace_metadata(case_id)`` in the message metadata; the
agent wraps its work in ``propagate_from_metadata(metadata, name)`` so its
spans nest under the same trace, or open a new case when none was passed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from sc_core.infra import tracing

TRACE_ID = "trace_id"
CASE_ID = "case_id"
PARENT_OBSERVATION_ID = "parent_observation_id"


def trace_metadata(case_id: str) -> dict[str, str]:
    meta = {CASE_ID: case_id}
    if trace_id := tracing.current_trace_id():
        meta[TRACE_ID] = trace_id
    if parent := tracing.current_observation_id():
        meta[PARENT_OBSERVATION_ID] = parent
    return meta


@contextmanager
def propagate_from_metadata(
    metadata: Mapping[str, Any] | None, name: str, *, fallback_case_id: str
) -> Iterator[Any]:
    """Continue the caller's trace when it sent one; otherwise start a case."""
    meta = dict(metadata or {})
    case_id = str(meta.get(CASE_ID) or fallback_case_id)
    trace_id = meta.get(TRACE_ID)
    if trace_id:
        with tracing.continue_trace(
            str(trace_id),
            name,
            case_id=case_id,
            parent_observation_id=meta.get(PARENT_OBSERVATION_ID),
        ) as span:
            yield span
    else:
        with tracing.start_case(case_id, name) as span:
            yield span
