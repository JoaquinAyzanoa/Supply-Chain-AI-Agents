"""Request id and context middleware.

Accepts an incoming ``X-Request-ID`` (so a caller or proxy can correlate) or
generates one, binds it together with the service name into
:mod:`sc_core.infra.context` for the duration of the request so every log
line carries them, and echoes the id on the response.

Implemented as a raw ASGI middleware rather than ``BaseHTTPMiddleware`` so
the context variables are set in the same task that runs the endpoint. The
service name is bound here, per request, because values set during the
lifespan live in a different task context and would not be visible.
"""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sc_core.infra import context

HEADER = "X-Request-ID"
# Accept only sane ids from callers; anything else is replaced.
_VALID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def new_request_id() -> str:
    return uuid4().hex


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp, *, service_name: str) -> None:
        self.app = app
        self.service_name = service_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(HEADER.lower())
        request_id = incoming if incoming and _VALID.match(incoming) else new_request_id()

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(HEADER, request_id)
            await send(message)

        with context.bind(request_id=request_id, service_name=self.service_name):
            await self.app(scope, receive, send_with_id)
