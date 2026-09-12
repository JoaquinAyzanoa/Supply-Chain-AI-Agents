"""Expose a ``TaskHandler`` as an A2A agent on a FastAPI app.

``mount(app, handler, spec, token=...)`` adds the agent card at
``/.well-known/agent-card.json`` (public) and the JSON-RPC endpoint at
``/a2a`` (bearer token required). Each request becomes one A2A task: the
executor enqueues the task, runs the handler under the caller's trace and
closes the task as completed, input-required (paused on an approval),
rejected or failed with the reply JSON as the status message.
"""

from __future__ import annotations

import json
from typing import Any

from a2a import types as a2a_types
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from fastapi import FastAPI
from loguru import logger
from starlette.types import ASGIApp, Receive, Scope, Send

from sc_core.a2a.protocol import AgentReply, AgentSpec, TaskHandler
from sc_core.a2a.trace import propagate_from_metadata
from sc_core.shared.errors import ConfigurationError, ScError

RPC_PATH = "/a2a"
CARD_PATH = "/.well-known/agent-card.json"


def build_card(spec: AgentSpec) -> a2a_types.AgentCard:
    return a2a_types.AgentCard(
        name=spec.name,
        description=spec.description,
        version=spec.version,
        supported_interfaces=[a2a_types.AgentInterface(url=spec.url, protocol_binding="JSONRPC")],
        capabilities=a2a_types.AgentCapabilities(streaming=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[
            a2a_types.AgentSkill(id=s.id, name=s.name, description=s.description, tags=s.tags)
            for s in spec.skills
        ],
    )


class HandlerExecutor(AgentExecutor):
    def __init__(self, handler: TaskHandler, *, agent_name: str) -> None:
        self._handler = handler
        self._agent_name = agent_name

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        if message is None:
            raise ScError("A2A request without a message")
        task_id, context_id = context.task_id or "", context.context_id or ""
        if context.current_task is None:
            await event_queue.enqueue_event(
                a2a_types.Task(
                    id=task_id,
                    context_id=context_id,
                    status=a2a_types.TaskStatus(state=a2a_types.TaskState.TASK_STATE_SUBMITTED),
                    history=[message],
                )
            )
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()
        metadata = dict(message.metadata) if message.HasField("metadata") else {}
        task_json = context.get_user_input()
        with propagate_from_metadata(
            metadata, f"{self._agent_name}.task", fallback_case_id=context_id
        ):
            try:
                reply = await self._handler.handle(task_json, metadata)
            except ScError as exc:
                logger.opt(exception=True).error("agent task failed: {}", exc.message)
                reply = AgentReply(status="failed", text=json.dumps(exc.to_dict()))
        await _close(updater, reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


async def _close(updater: TaskUpdater, reply: AgentReply) -> None:
    message = updater.new_agent_message(parts=[a2a_types.Part(text=reply.text)])
    if reply.status == "completed":
        await updater.complete(message=message)
    elif reply.status == "input_required":
        await updater.requires_input(message=message)
    elif reply.status == "rejected":
        await updater.reject(message=message)
    elif reply.status == "canceled":
        await updater.cancel(message=message)
    else:
        await updater.failed(message=message)


class BearerAuthMiddleware:
    """Pure ASGI: ``Authorization: Bearer <token>`` required on protected path prefixes."""

    def __init__(self, app: ASGIApp, *, token: str, protected: tuple[str, ...] = (RPC_PATH,)):
        if not token:
            raise ConfigurationError("A2A bearer token is empty; set SC__A2A__TOKEN")
        self.app = app
        self._token = token
        self._protected = protected

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self._protected):
            header = dict(scope.get("headers") or {}).get(b"authorization", b"").decode()
            if header != f"Bearer {self._token}":
                await _unauthorized(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def _unauthorized(scope: Scope, receive: Receive, send: Send) -> None:
    body = b'{"error": "unauthorized"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def mount(app: FastAPI, handler: TaskHandler, spec: AgentSpec, *, token: str) -> None:
    """Add the agent card, the JSON-RPC endpoint and the bearer check to ``app``."""
    card = build_card(spec)
    request_handler = DefaultRequestHandler(
        agent_executor=HandlerExecutor(handler, agent_name=spec.name),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card, card_url=CARD_PATH),
        jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url=RPC_PATH),
    )
    app.add_middleware(BearerAuthMiddleware, token=token)


def task_state_name(state: Any) -> str:
    return str(a2a_types.TaskState.Name(state))
