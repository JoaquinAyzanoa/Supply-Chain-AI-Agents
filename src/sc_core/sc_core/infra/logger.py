"""Structured logging on top of loguru.

Design:

- One sink. Human-readable in ``dev``, one JSON object per line elsewhere
  (``Settings.use_json_logs``).
- Context injection. ``service_name``, ``case_id``, ``trace_id``, ``run_id``
  and ``request_id`` from :mod:`sc_core.infra.context` are added to every
  record automatically, so callers never pass them by hand.
- Redaction. Any ``extra`` field whose key looks sensitive (email bodies,
  secrets, tokens) is replaced by ``[REDACTED]`` before it reaches the sink.
  This is the guarantee behind "no email body is ever written to a log".
- Standard-library interception. Libraries that use ``logging`` (uvicorn,
  httpx, msal) are routed through the same sink and format.

Usage: call :func:`configure_logging` once at process start, then
``from loguru import logger`` anywhere.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
from collections.abc import Callable, Mapping
from datetime import datetime
from types import FrameType
from typing import TYPE_CHECKING, Any, TextIO

from loguru import logger

from sc_core.infra import context
from sc_core.infra.settings import Settings

if TYPE_CHECKING:
    from loguru import Record

# Any extra key containing one of these substrings (case-insensitive) is redacted.
# "body" covers html_body / inbound body; "text" alone is deliberately not listed
# because it would hide harmless fields such as "context".
REDACT_KEY_PARTS: tuple[str, ...] = (
    "body",
    "inbound_text",
    "attachments_text",
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "token",
    "authorization",
    "cookie",
)
REDACTED = "[REDACTED]"

_HUMAN_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
    "<cyan>{extra[service_name]}</cyan> "
    "<magenta>{extra[case_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level> {extra[_extra_repr]}"
)

_CONTEXT_KEYS = ("service_name", "case_id", "trace_id", "run_id", "request_id")

# Fallback for records emitted outside any request context (uvicorn startup,
# scheduler ticks, background tasks). Set by configure_logging.
_default_service_name = "unnamed"


def is_sensitive_key(key: str) -> bool:
    """True when ``key`` names something that must never be logged in clear."""
    lowered = key.lower()
    return any(part in lowered for part in REDACT_KEY_PARTS)


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with sensitive keys replaced, recursively.

    Dicts are walked by key; lists and tuples by element. Scalars are
    returned unchanged because redaction is key-based, not content-based.
    """
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and is_sensitive_key(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact(v) for v in value)
    return value


def _patch_record(record: Record) -> None:
    """loguru patcher: inject context and redact extras, in place."""
    extra = record["extra"]
    snap = context.snapshot()
    if snap["service_name"] == "unnamed":
        snap["service_name"] = _default_service_name
    for key in _CONTEXT_KEYS:
        extra.setdefault(key, snap[key])
    for key in list(extra):
        if key in _CONTEXT_KEYS:
            continue
        extra[key] = REDACTED if is_sensitive_key(key) else redact(extra[key])
    user_extra = {k: v for k, v in extra.items() if k not in _CONTEXT_KEYS}
    extra["_extra_repr"] = json.dumps(user_extra, default=str) if user_extra else ""


def _json_sink(stream: TextIO) -> Callable[[Any], None]:
    """Build a sink that writes one JSON object per record to ``stream``."""

    def sink(message: Any) -> None:
        record = message.record
        extra = record["extra"]
        payload: dict[str, Any] = {
            "ts": _iso(record["time"]),
            "level": record["level"].name,
            "msg": record["message"],
            "logger": f"{record['name']}:{record['function']}:{record['line']}",
        }
        for key in _CONTEXT_KEYS:
            payload[key] = extra.get(key)
        user_extra = {
            k: v for k, v in extra.items() if k not in _CONTEXT_KEYS and k != "_extra_repr"
        }
        if user_extra:
            payload["extra"] = user_extra
        if record["exception"] is not None:
            payload["exception"] = str(record["exception"])
        stream.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")

    return sink


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


class InterceptHandler(logging.Handler):
    """Route standard-library ``logging`` records into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # Walk out of the logging module so loguru attributes the record to
        # the real caller (uvicorn, httpx, ...) instead of logging internals.
        frame: FrameType | None = inspect.currentframe()
        depth = 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    """Install the single sink for this process.

    ``stream`` defaults to stderr; tests pass a ``StringIO`` to capture output.
    Calling it twice replaces the previous configuration, which keeps it
    idempotent for tests and for services that reconfigure on reload.
    """
    global _default_service_name
    out = stream or sys.stderr
    _default_service_name = settings.service_name
    context.service_name.set(settings.service_name)
    logger.remove()
    logger.configure(patcher=_patch_record)
    if settings.use_json_logs:
        logger.add(_json_sink(out), level=settings.log_level)
    else:
        logger.add(out, level=settings.log_level, format=_HUMAN_FORMAT, colorize=out is sys.stderr)

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "httpx", "msal"):
        logging.getLogger(name).handlers = [InterceptHandler()]
        logging.getLogger(name).propagate = False
    # httpx logs every request at INFO; our clients log what matters themselves.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # uvicorn's access log is opt-in (our middleware already gives every
    # request an id); attaching a handler here would silently re-enable it.
    access = logging.getLogger("uvicorn.access")
    access.handlers = [InterceptHandler()] if settings.http.access_log else []
    access.propagate = False
