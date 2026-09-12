"""A2A entry point: a task in, a result out.

The task JSON is validated before anything runs; an invalid task is a
``failed`` reply without a graph call. A run paused on an approval answers
``input_required`` so the caller knows a person is in the loop.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from loguru import logger
from pydantic import ValidationError

from sc_core.a2a import AgentReply, AgentSpec, Skill
from sc_core.a2a.protocol import ReplyStatus
from sc_core.schema.a2a import SupplierCommsResult, SupplierCommsTask
from supplier_comms import AGENT_NAME, __version__
from supplier_comms.service import AgentProvider

SKILLS = [
    Skill(
        id="send_rfq",
        name="Send RFQ",
        description="Draft and send a request for quotation",
        tags=["outbound"],
    ),
    Skill(
        id="request_eta",
        name="Request ETA",
        description="Ask the supplier for a delivery date",
        tags=["outbound"],
    ),
    Skill(
        id="follow_up", name="Follow up", description="Remind a silent supplier", tags=["outbound"]
    ),
    Skill(
        id="handle_inbound",
        name="Handle inbound",
        description="Interpret a supplier reply and propose order changes",
        tags=["inbound"],
    ),
    Skill(
        id="resolve_unlinked",
        name="Resolve unlinked",
        description="Attach a message the rules could not link",
        tags=["inbound"],
    ),
]


def agent_spec(public_url: str) -> AgentSpec:
    return AgentSpec(
        name=AGENT_NAME,
        description="Supplier communications: RFQs, follow-ups, replies and order changes "
        "with human approval",
        version=__version__,
        url=public_url.rstrip("/") + "/a2a",
        skills=SKILLS,
    )


class SupplierCommsHandler:
    def __init__(self, provider: AgentProvider) -> None:
        self._provider = provider

    async def handle(self, task_json: str, metadata: Mapping[str, Any]) -> AgentReply:
        try:
            task = SupplierCommsTask.model_validate_json(task_json)
        except ValidationError as exc:
            logger.warning("invalid task: {}", exc.errors()[:3])
            return AgentReply(
                status="failed",
                text=json.dumps({"code": "invalid_task", "errors": exc.errors()}, default=str),
            )
        agent = await self._provider.get()
        result = await agent.run(task)
        return reply_for(result)


def reply_for(result: SupplierCommsResult) -> AgentReply:
    status: ReplyStatus
    if result.status == "awaiting_approval":
        status = "input_required"
    elif result.status == "failed":
        status = "failed"
    else:
        status = "completed"
    return AgentReply(status=status, text=result.model_dump_json())
