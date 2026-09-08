"""Translate Odoo JSON-RPC failures into the project's error hierarchy.

Odoo answers a failed call with HTTP 200 and an ``error`` object whose
``data.name`` is the Python exception class raised server-side, for example
``odoo.exceptions.AccessError``. That name, not the HTTP status, decides
what the caller should do:

- access and missing records are the caller's problem (4xx-like, no retry)
- validation and user errors are business rules (no retry)
- anything else (a bug in an addon, a database error) is reported as an
  external service failure with the server traceback kept for logs
"""

from __future__ import annotations

from typing import Any

from sc_core.shared.errors import (
    BusinessRuleViolation,
    ExternalServiceError,
    Forbidden,
    NotFound,
    ScError,
    ValidationFailed,
)

SERVICE = "odoo"

# Exception class name reported by Odoo -> our error type.
_BY_NAME: dict[str, type[ScError]] = {
    "odoo.exceptions.AccessError": Forbidden,
    "odoo.exceptions.AccessDenied": Forbidden,
    "odoo.exceptions.MissingError": NotFound,
    "odoo.exceptions.ValidationError": ValidationFailed,
    "odoo.exceptions.UserError": BusinessRuleViolation,
    "odoo.exceptions.RedirectWarning": BusinessRuleViolation,
}

_TRACEBACK_MAX = 4000


class OdooRpcError(ExternalServiceError):
    """Odoo failed in a way that is neither access nor business related."""

    code = "odoo_rpc_error"


def translate_rpc_error(error: dict[str, Any]) -> ScError:
    """Build the right exception for a JSON-RPC ``error`` object (never raises)."""
    data = error.get("data") or {}
    name = str(data.get("name") or "")
    message = str(data.get("message") or error.get("message") or "Odoo error")
    details: dict[str, Any] = {"odoo_exception": name}
    if data.get("arguments"):
        details["arguments"] = [str(a)[:500] for a in data["arguments"]]

    cls = _BY_NAME.get(name)
    if cls is not None:
        return cls(message, details=details)

    debug = str(data.get("debug") or "")
    if debug:
        details["traceback"] = debug[-_TRACEBACK_MAX:]
    # A server-side bug will not fix itself on retry.
    return OdooRpcError(message, service=SERVICE, details=details, retryable=False)
