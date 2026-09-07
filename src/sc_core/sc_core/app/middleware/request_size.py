"""Request body size limit.

The body is read here, before the application runs, so that an oversized
request is answered with a clean 413 and never reaches a handler. Reading
up front is acceptable because the limit is small (1 MiB by default: JSON
events and webhooks). Two paths:

1. A declared ``Content-Length`` above the limit is rejected immediately
   without reading anything.
2. Otherwise chunks are consumed until either the body ends (it is then
   replayed to the app as a single message) or the running total crosses the
   limit (413).

Raising from ``receive`` was considered and rejected: FastAPI turns any
exception raised while parsing a body into a generic 400, which would hide
the real reason from the client.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CODE = "payload_too_large"


def _too_large(limit: int) -> JSONResponse:
    return JSONResponse(
        {
            "code": CODE,
            "message": f"request body exceeds {limit} bytes",
            "retryable": False,
            "details": {"max_bytes": limit},
        },
        status_code=413,
    )


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _too_large(self.max_bytes)(scope, receive, send)
            return

        chunks: list[bytes] = []
        total = 0
        pending: Message | None = None
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # Client went away (http.disconnect): hand the message to the app as-is.
                pending = message
                break
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await _too_large(self.max_bytes)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        replay: list[Message] = []
        if pending is None:
            replay.append({"type": "http.request", "body": b"".join(chunks), "more_body": False})
        else:
            replay.append(pending)

        async def replay_receive() -> Message:
            if replay:
                return replay.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)
