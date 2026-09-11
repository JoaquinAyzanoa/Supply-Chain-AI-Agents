"""Mail-specific errors."""

from __future__ import annotations

from sc_core.shared.errors import ExternalServiceError, ScError

SERVICE = "graph"


class MailAuthRequired(ScError):
    """No usable session: someone must run ``just mail-login`` (delegated mode)."""

    code = "mail_auth_required"


class GraphError(ExternalServiceError):
    """Microsoft Graph or the identity platform failed."""

    code = "graph_error"

    def __init__(
        self, message: str, *, details: dict | None = None, retryable: bool = True
    ) -> None:
        super().__init__(message, service=SERVICE, details=details, retryable=retryable)


class DeltaExpired(GraphError):
    """The stored delta link is no longer valid (HTTP 410); a full resync is needed."""

    code = "graph_delta_expired"

    def __init__(self) -> None:
        super().__init__("delta link expired; full resync required", retryable=False)
