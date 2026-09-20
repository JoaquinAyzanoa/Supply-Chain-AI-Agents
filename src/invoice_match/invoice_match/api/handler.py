"""A2A entry point: a task in, a result out."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from loguru import logger
from pydantic import ValidationError

from invoice_match import AGENT_NAME, __version__
from invoice_match.infra.service import AgentProvider
from sc_core.a2a import AgentReply, AgentSpec, Skill
from sc_core.a2a.protocol import ReplyStatus
from sc_core.schema.a2a import InvoiceMatchResult, InvoiceMatchTask

SKILLS = [
    Skill(
        id="match_bill",
        name="Match bill",
        description="Read a supplier invoice, match it to the order and the receipts, "
        "and draft the bill or hold it with the variance table",
        tags=["invoice"],
    ),
]


def agent_spec(public_url: str) -> AgentSpec:
    return AgentSpec(
        name=AGENT_NAME,
        description="Invoice matching: supplier invoices against orders and receipts; draft "
        "bills for clean ones, a hold otherwise, never posted",
        version=__version__,
        url=public_url.rstrip("/") + "/a2a",
        skills=SKILLS,
    )


class InvoiceMatchHandler:
    def __init__(self, provider: AgentProvider) -> None:
        self._provider = provider

    async def handle(self, task_json: str, metadata: Mapping[str, Any]) -> AgentReply:
        try:
            task = InvoiceMatchTask.model_validate_json(task_json)
        except ValidationError as exc:
            logger.warning("invalid task: {}", exc.errors()[:3])
            return AgentReply(
                status="failed",
                text=json.dumps({"code": "invalid_task", "errors": exc.errors()}, default=str),
            )
        agent = await self._provider.get()
        result = await agent.run(task)
        return reply_for(result)


def reply_for(result: InvoiceMatchResult) -> AgentReply:
    status: ReplyStatus
    if result.status == "awaiting_approval":
        status = "input_required"
    elif result.status == "failed":
        status = "failed"
    else:
        status = "completed"
    return AgentReply(status=status, text=result.model_dump_json())
