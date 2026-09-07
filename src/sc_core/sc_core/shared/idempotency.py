"""Identifiers and idempotency keys.

Two kinds of ids:

- ``new_id(prefix)``: random, for things that are created once (an event,
  a run).
- ``deterministic_id(prefix, *parts)``: derived from its inputs, for things
  that must be created at most once per input (a case for a given inbound
  email, an RFQ for a given planning run and supplier). Retrying with the
  same inputs yields the same id, which is what makes writes idempotent.

``make_key`` is the underlying hash; use it directly as the ``external_ref``
stored in Odoo.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

_PREFIX_MAX = 24


def _canonical(parts: tuple[Any, ...]) -> str:
    # Sorted keys and no whitespace make dict order irrelevant; ``default=str``
    # covers Decimal, datetime, UUID. Type is preserved through JSON so that
    # 1 and "1" produce different keys, as they should.
    return json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def make_key(namespace: str, *parts: Any) -> str:
    """Stable SHA-256 hex digest of ``namespace`` and ``parts`` (order-sensitive)."""
    if not namespace:
        raise ValueError("namespace must not be empty")
    payload = _canonical((namespace, *parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _check_prefix(prefix: str) -> None:
    if not prefix or len(prefix) > _PREFIX_MAX or not prefix.replace("_", "").isalnum():
        raise ValueError(
            f"prefix must be 1-{_PREFIX_MAX} alphanumeric/underscore chars, got {prefix!r}"
        )


def deterministic_id(prefix: str, *parts: Any, length: int = 16) -> str:
    """``<prefix>_<hash>`` where the hash is derived from ``parts``."""
    _check_prefix(prefix)
    if not 8 <= length <= 64:
        raise ValueError("length must be between 8 and 64")
    return f"{prefix}_{make_key(prefix, *parts)[:length]}"


def new_id(prefix: str) -> str:
    """``<prefix>_<uuid4 hex>``, random."""
    _check_prefix(prefix)
    return f"{prefix}_{uuid4().hex}"
