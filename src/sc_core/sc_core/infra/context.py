"""Request-scoped context carried through ``contextvars``.

Every log line, trace and outbound call should be attributable to a business
case. Instead of threading ``case_id`` through every function signature, the
identifiers live in context variables that follow the current task across
``await`` boundaries. ``bind`` sets them for a block of code and restores the
previous values on exit, so nested cases and concurrent tasks never leak into
each other.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

service_name: ContextVar[str] = ContextVar("service_name", default="unnamed")
case_id: ContextVar[str | None] = ContextVar("case_id", default=None)
trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

_VARS: dict[str, ContextVar[Any]] = {
    "service_name": service_name,
    "case_id": case_id,
    "trace_id": trace_id,
    "run_id": run_id,
    "request_id": request_id,
}


def snapshot() -> dict[str, Any]:
    """Current values of every context variable, including unset ones as ``None``."""
    return {name: var.get() for name, var in _VARS.items()}


@contextmanager
def bind(**values: Any) -> Iterator[None]:
    """Set context variables for the duration of the block.

    Unknown names raise ``KeyError`` immediately rather than being silently
    ignored, so a typo in ``bind(cas_id=...)`` fails at the call site.
    """
    unknown = set(values) - set(_VARS)
    if unknown:
        raise KeyError(f"unknown context variables: {sorted(unknown)}")
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = []
    try:
        for name, value in values.items():
            var = _VARS[name]
            tokens.append((var, var.set(value)))
        yield
    finally:
        # Reset in reverse order so nested binds unwind correctly.
        for var, token in reversed(tokens):
            var.reset(token)
