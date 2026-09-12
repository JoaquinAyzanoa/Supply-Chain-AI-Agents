"""Doubles for agent callers.

``FakeAgentCaller`` answers from a script and records what was sent, for
orchestrator tests. ``in_process_client(app)`` is a real ``A2AClient`` over
an ASGI transport, for tests that exercise a mounted agent without a server.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI

from sc_core.a2a.client import A2AClient
from sc_core.a2a.protocol import AgentReply


@dataclass
class SentTask:
    task_json: str
    case_id: str
    metadata: dict[str, Any]


@dataclass
class FakeAgentCaller:
    replies: list[AgentReply | Exception] = field(default_factory=list)
    sent: list[SentTask] = field(default_factory=list)

    async def send(
        self, task_json: str, *, case_id: str, metadata: Mapping[str, Any] | None = None
    ) -> AgentReply:
        self.sent.append(SentTask(task_json, case_id, dict(metadata or {})))
        if not self.replies:
            raise AssertionError("FakeAgentCaller has no scripted reply left")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def in_process_client(
    app: FastAPI, *, token: str, base_url: str = "http://agent.test"
) -> A2AClient:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)
    return A2AClient(base_url, token=token, http=http)
