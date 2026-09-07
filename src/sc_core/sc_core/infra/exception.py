"""Re-export of the domain error hierarchy for infrastructure code.

The hierarchy lives in :mod:`sc_core.shared.errors` so that pure domain code
can import it without pulling infrastructure modules. This module exists so
that ``sc_core.infra`` reads as a complete layer and later gains the FastAPI
exception handlers (story P0-S3) next to the errors they translate.
"""

from sc_core.shared.errors import (
    ApprovalRequired,
    BudgetExceeded,
    BusinessRuleViolation,
    ConfigurationError,
    Conflict,
    ExternalServiceError,
    Forbidden,
    NotFound,
    ScError,
    ValidationFailed,
)

__all__ = [
    "ApprovalRequired",
    "BudgetExceeded",
    "BusinessRuleViolation",
    "ConfigurationError",
    "Conflict",
    "ExternalServiceError",
    "Forbidden",
    "NotFound",
    "ScError",
    "ValidationFailed",
]
