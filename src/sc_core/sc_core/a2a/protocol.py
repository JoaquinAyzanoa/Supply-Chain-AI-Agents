"""What crosses the A2A boundary, independent of the SDK's protobuf types.

An agent receives a task as JSON text plus a metadata dict (trace and case
ids) and answers with an ``AgentReply``: a status and JSON text. The typed
task and result schemas per agent live in ``sc_core.schema.a2a``; this
module only carries them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol

from sc_core.schema.base import StrictModel

ReplyStatus = Literal["completed", "input_required", "failed", "rejected", "canceled"]


class AgentReply(StrictModel):
    status: ReplyStatus
    text: str = ""  # JSON of the agent's result model
    task_id: str | None = None
    context_id: str | None = None


class TaskHandler(Protocol):
    async def handle(self, task_json: str, metadata: Mapping[str, Any]) -> AgentReply: ...


class Skill(StrictModel):
    id: str
    name: str
    description: str
    tags: list[str] = []


class AgentSpec(StrictModel):
    """What the agent card advertises."""

    name: str
    description: str
    version: str
    url: str  # the RPC endpoint as other services reach it, e.g. http://supplier_comms:8000/a2a
    skills: list[Skill]
