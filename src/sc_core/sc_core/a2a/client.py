"""Call an agent over A2A.

``A2AClient(base_url, token=...)`` sends one task (JSON text) with the
current trace metadata and returns the agent's ``AgentReply``. The agent
card is not fetched: the RPC endpoint is known (``<base_url>/a2a``), which
keeps a call to one HTTP request.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import uuid4

import httpx
from a2a import types as a2a_types
from a2a.client import ClientConfig, ClientFactory, minimal_agent_card
from google.protobuf.struct_pb2 import Struct
from loguru import logger

from sc_core.a2a.protocol import AgentReply, ReplyStatus
from sc_core.a2a.server import RPC_PATH
from sc_core.a2a.trace import trace_metadata
from sc_core.shared.errors import ExternalServiceError

_STATES: dict[int, ReplyStatus] = {
    a2a_types.TaskState.TASK_STATE_COMPLETED: "completed",
    a2a_types.TaskState.TASK_STATE_INPUT_REQUIRED: "input_required",
    a2a_types.TaskState.TASK_STATE_FAILED: "failed",
    a2a_types.TaskState.TASK_STATE_REJECTED: "rejected",
    a2a_types.TaskState.TASK_STATE_CANCELED: "canceled",
}


class AgentCaller(Protocol):
    async def send(self, task_json: str, *, case_id: str) -> AgentReply: ...


class A2AClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        timeout_seconds: float = 300.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.rpc_url = base_url.rstrip("/") + RPC_PATH
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)
        self._http.headers["Authorization"] = f"Bearer {token}"
        self._http.timeout = httpx.Timeout(timeout_seconds)
        self._client: Any | None = None

    async def _sdk_client(self) -> Any:
        if self._client is None:
            config = ClientConfig(
                httpx_client=self._http, streaming=False, supported_protocol_bindings=["JSONRPC"]
            )
            card = minimal_agent_card(self.rpc_url, ["JSONRPC"])
            self._client = ClientFactory(config).create(card)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
        await self._http.aclose()

    async def send(
        self, task_json: str, *, case_id: str, metadata: Mapping[str, Any] | None = None
    ) -> AgentReply:
        meta = Struct()
        meta.update({**trace_metadata(case_id), **(metadata or {})})
        message = a2a_types.Message(
            message_id=uuid4().hex,
            role=a2a_types.Role.ROLE_USER,
            parts=[a2a_types.Part(text=task_json)],
            metadata=meta,
        )
        client = await self._sdk_client()
        final: a2a_types.Task | None = None
        try:
            async for response in client.send_message(
                a2a_types.SendMessageRequest(message=message)
            ):
                if response.HasField("task"):
                    final = response.task
                elif response.HasField("message"):
                    text = "".join(p.text for p in response.message.parts)
                    return AgentReply(status="completed", text=text)
        except httpx.HTTPStatusError as exc:
            raise ExternalServiceError(
                f"agent answered {exc.response.status_code}",
                service="a2a",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"agent unreachable: {exc}", service="a2a") from exc
        except Exception as exc:  # the SDK maps JSON-RPC errors to its own exceptions
            raise ExternalServiceError(
                f"a2a call failed: {type(exc).__name__}: {exc}", service="a2a", retryable=False
            ) from exc
        if final is None:
            raise ExternalServiceError("agent returned no task", service="a2a", retryable=False)
        return reply_from_task(final)


def reply_from_task(task: a2a_types.Task) -> AgentReply:
    status = _STATES.get(task.status.state, "failed")
    text = ""
    if task.status.HasField("message"):
        text = "".join(p.text for p in task.status.message.parts)
    logger.bind(task_id=task.id, status=status).debug("a2a reply")
    return AgentReply(status=status, text=text, task_id=task.id, context_id=task.context_id)
