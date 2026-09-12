"""Langfuse tracing: one trace per business case, spans for every step.

Vocabulary:

- **case**: the unit of work a human can follow (an inbound email, a daily
  planning run). ``start_case`` opens the root span of a new trace and sets
  the Langfuse ``session_id`` to the case id, so every trace of the same
  purchase order groups together.
- **continue_trace**: attaches work in another process (an agent reached
  over A2A) to the trace the caller started; the ids travel in the A2A
  metadata (phase 5).
- **span / tool**: any step worth seeing in the timeline, with its full
  input and output.

When Langfuse is not configured the client is created with tracing
disabled: every call becomes a no-op, so code never branches on it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse, propagate_attributes
from langfuse.types import TraceContext

from sc_core.infra import context
from sc_core.infra.settings import Settings

_client: Langfuse | None = None
_host: str = ""


def configure_tracing(settings: Settings) -> Langfuse:
    """Create the process-wide Langfuse client (idempotent)."""
    global _client, _host
    cfg = settings.langfuse
    _host = cfg.browser_url if cfg.configured else ""  # links are for people's browsers
    _client = Langfuse(
        public_key=cfg.public_key or "pk-disabled",
        secret_key=cfg.secret_key.get_secret_value() or "sk-disabled",
        base_url=cfg.host,
        environment=settings.environment,
        tracing_enabled=cfg.configured,
        release=settings.service_name,
    )
    return _client


def trace_url(trace_id: str | None) -> str | None:
    """Link to the trace in the Langfuse UI (``public_url``), or ``None`` when tracing is off."""
    if not trace_id or not _host:
        return None
    return f"{_host}/trace/{trace_id}"


def tracer() -> Langfuse:
    """The configured client; a disabled one when ``configure_tracing`` was never called."""
    global _client
    if _client is None:
        _client = Langfuse(
            public_key="pk-disabled", secret_key="sk-disabled", tracing_enabled=False
        )
    return _client


def current_trace_id() -> str | None:
    return tracer().get_current_trace_id()


def current_observation_id() -> str | None:
    return tracer().get_current_observation_id()


@contextmanager
def start_case(
    case_id: str,
    name: str,
    *,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[Any]:
    """Root span of a new trace for ``case_id``; binds the ids into the log context."""
    lf = tracer()
    with (
        propagate_attributes(
            session_id=case_id,
            trace_name=name,
            metadata=metadata,
            tags=tags,
        ),
        lf.start_as_current_observation(name=name, as_type="span", input=input) as span,
        context.bind(case_id=case_id, trace_id=lf.get_current_trace_id()),
    ):
        yield span


@contextmanager
def continue_trace(
    trace_id: str,
    name: str,
    *,
    case_id: str,
    parent_observation_id: str | None = None,
    input: Any = None,
) -> Iterator[Any]:
    """Nest work under a trace started elsewhere (ids received over A2A)."""
    lf = tracer()
    trace_context: TraceContext = {"trace_id": trace_id}
    if parent_observation_id:
        trace_context["parent_span_id"] = parent_observation_id
    with (
        propagate_attributes(session_id=case_id),
        lf.start_as_current_observation(
            name=name, as_type="span", input=input, trace_context=trace_context
        ) as span,
        context.bind(case_id=case_id, trace_id=trace_id),
    ):
        yield span


@contextmanager
def span(name: str, *, input: Any = None) -> Iterator[Any]:
    """A step inside the current trace. Call ``.update(output=...)`` on the yielded span."""
    with tracer().start_as_current_observation(name=name, as_type="span", input=input) as obs:
        yield obs


@contextmanager
def tool_span(name: str, *, input: Any = None) -> Iterator[Any]:
    """A tool execution (Odoo lookup, Graph call) with its arguments and result."""
    with tracer().start_as_current_observation(
        name=f"tool.{name}", as_type="tool", input=input
    ) as obs:
        yield obs


def trace_metadata() -> dict[str, str]:
    """Ids to forward to another process (A2A message metadata)."""
    data: dict[str, str] = {}
    if trace_id := current_trace_id():
        data["trace_id"] = trace_id
    if observation_id := current_observation_id():
        data["parent_observation_id"] = observation_id
    if case_id := context.case_id.get():
        data["case_id"] = case_id
    return data


def flush() -> None:
    tracer().flush()
