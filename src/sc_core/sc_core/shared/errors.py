"""Domain error hierarchy shared by every service.

Rules:

- Raise these, not bare ``Exception`` or library exceptions, at package
  boundaries (Odoo client, Graph client, LLM layer, repositories).
- ``retryable`` tells callers whether a retry could succeed. Business errors
  are never retryable; transport errors usually are.
- ``details`` carries structured context for logs and API responses. Never
  put email bodies or secrets in it; the logger redacts by key, not content.
"""

from __future__ import annotations

from typing import Any


class ScError(Exception):
    """Base class for every error raised by this project."""

    code: str = "sc_error"
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serializable form used by API error responses and logs."""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"{type(self).__name__}({self.message!r}, details={self.details!r})"


class ConfigurationError(ScError):
    """A setting is missing or invalid. Raised at startup, never at runtime."""

    code = "configuration_error"


class ValidationFailed(ScError):
    """Input or data did not satisfy a schema or a business invariant."""

    code = "validation_failed"


class NotFound(ScError):
    """The referenced record does not exist."""

    code = "not_found"


class Conflict(ScError):
    """The operation collides with existing state (duplicate, stale version)."""

    code = "conflict"


class Forbidden(ScError):
    """The caller or the bot user is not allowed to perform the operation."""

    code = "forbidden"


class BusinessRuleViolation(ScError):
    """A downstream system rejected the operation on business grounds (e.g. Odoo UserError)."""

    code = "business_rule_violation"


class ExternalServiceError(ScError):
    """A dependency (Odoo, Graph, LLM provider, Langfuse) failed or timed out."""

    code = "external_service_error"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        service: str,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message, details={"service": service, **(details or {})})
        self.service = service
        if retryable is not None:
            self.retryable = retryable


class BudgetExceeded(ScError):
    """A run would exceed its token or cost budget. Never retry; escalate."""

    code = "budget_exceeded"


class ApprovalRequired(ScError):
    """A write was attempted without a resolved approval. Programming error."""

    code = "approval_required"
