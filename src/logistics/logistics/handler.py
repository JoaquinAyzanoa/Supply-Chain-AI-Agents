"""A2A entry point: a task in, a result out."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from loguru import logger
from pydantic import ValidationError

from logistics import AGENT_NAME, __version__
from logistics.service import AgentProvider
from sc_core.a2a import AgentReply, AgentSpec, Skill
from sc_core.a2a.protocol import ReplyStatus
from sc_core.schema.a2a import LogisticsResult, LogisticsTask

SKILLS = [
    Skill(
        id="track_shipment",
        name="Track shipment",
        description="Read a shipping notice and propose the arrival date on the order",
        tags=["inbound"],
    ),
    Skill(
        id="reconcile_receipt",
        name="Reconcile receipt",
        description="Compare a validated receipt with the order and report discrepancies",
        tags=["receipt"],
    ),
    Skill(
        id="report_discrepancy",
        name="Report discrepancy",
        description="Write to the supplier about a receipt problem a person described",
        tags=["receipt", "outbound"],
    ),
]


def agent_spec(public_url: str) -> AgentSpec:
    return AgentSpec(
        name=AGENT_NAME,
        description="Logistics: shipping notices, receipt reconciliation and discrepancy "
        "reports with human approval",
        version=__version__,
        url=public_url.rstrip("/") + "/a2a",
        skills=SKILLS,
    )


class LogisticsHandler:
    def __init__(self, provider: AgentProvider) -> None:
        self._provider = provider

    async def handle(self, task_json: str, metadata: Mapping[str, Any]) -> AgentReply:
        try:
            task = LogisticsTask.model_validate_json(task_json)
        except ValidationError as exc:
            logger.warning("invalid task: {}", exc.errors()[:3])
            return AgentReply(
                status="failed",
                text=json.dumps({"code": "invalid_task", "errors": exc.errors()}, default=str),
            )
        agent = await self._provider.get()
        result = await agent.run(task)
        return reply_for(result)


def reply_for(result: LogisticsResult) -> AgentReply:
    status: ReplyStatus
    if result.status == "awaiting_approval":
        status = "input_required"
    elif result.status == "failed":
        status = "failed"
    else:
        status = "completed"
    return AgentReply(status=status, text=result.model_dump_json())
