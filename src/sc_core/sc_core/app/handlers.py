"""Error handling: domain errors and crashes become consistent JSON responses.

Mapping rationale:

- Client-side problems (not found, validation, business rule) get 4xx and
  are not retryable.
- ``ExternalServiceError`` becomes 502: this service is fine, a dependency
  is not; callers may retry. Its details (which service) are safe to return.
- ``BudgetExceeded`` is 429: the caller must slow down or escalate.
- Programming errors (``ApprovalRequired``, ``ConfigurationError``) and
  anything unexpected are 500, logged with the traceback, and the response
  carries only the request id so nothing internal leaks.

Unexpected exceptions are caught by :class:`UnhandledErrorMiddleware`, which
sits inside our middleware chain. Starlette's own catch-all runs outside
every middleware, so a handler registered there would produce responses
without the request id header or the security headers.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sc_core.infra import context
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

STATUS_BY_ERROR: dict[type[ScError], int] = {
    NotFound: 404,
    Conflict: 409,
    Forbidden: 403,
    ValidationFailed: 422,
    BusinessRuleViolation: 422,
    BudgetExceeded: 429,
    ExternalServiceError: 502,
    ApprovalRequired: 500,
    ConfigurationError: 500,
}


def status_for(error: ScError) -> int:
    """Resolve the HTTP status for an error, honouring subclassing."""
    for cls in type(error).__mro__:
        if cls in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[cls]  # type: ignore[index]
    return 500


def internal_error_body(
    code: str = "internal_error", *, retryable: bool = False
) -> dict[str, object]:
    return {
        "code": code,
        "message": "internal error",
        "retryable": retryable,
        "details": {},
        "request_id": context.request_id.get(),
    }


def _body(error: ScError, status: int) -> dict[str, object]:
    if status == 500:
        return internal_error_body(error.code, retryable=error.retryable)
    payload: dict[str, object] = error.to_dict()
    payload["request_id"] = context.request_id.get()
    return payload


async def sc_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ScError)
    status = status_for(error)
    log = logger.bind(code=error.code, status=status, path=request.url.path)
    if status == 500:
        log.opt(exception=error).error("domain error treated as server failure")
    else:
        log.info("domain error: {}", error.message)
    return JSONResponse(_body(error, status), status_code=status)


class UnhandledErrorMiddleware:
    """Turn any exception escaping the app into a 500 JSON response.

    If the response already started streaming there is nothing safe to send,
    so the exception is re-raised for the server to close the connection.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_tracking(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking)
        except Exception as exc:  # noqa: BLE001 - this is the catch-all by design
            logger.bind(path=scope.get("path")).opt(exception=exc).error("unhandled exception")
            if response_started:
                raise
            await JSONResponse(internal_error_body(), status_code=500)(scope, receive, send)


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ScError, sc_error_handler)
