"""A2A entry point: a task in, a result out."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from loguru import logger
from pydantic import ValidationError

from sc_core.a2a import AgentReply, AgentSpec, Skill
from sc_core.a2a.protocol import ReplyStatus
from sc_core.schema.a2a import SourcingResult, SourcingTask
from sourcing import AGENT_NAME, __version__
from sourcing.service import AgentProvider

SKILLS = [
    Skill(
        id="quote_round",
        name="Quote round",
        description="Invite the best-ranked suppliers to quote a basket through the supplier "
        "agent; RFQs are created in Odoo as alternatives of one another",
        tags=["sourcing"],
    ),
    Skill(
        id="compare_quotes",
        name="Compare quotes",
        description="Compare the quotes of a round on landed cost, lead time and score and ask "
        "a person to award; the winner's RFQ is confirmed, the others declined",
        tags=["sourcing"],
    ),
    Skill(
        id="counter_offer",
        name="Counter-offer",
        description="Propose a better price on a quoted line within the buyer's cap and round "
        "limit; sent through the supplier agent after approval",
        tags=["negotiation"],
    ),
    Skill(
        id="alternate_source",
        name="Alternative source",
        description="For a late order, propose another supplier from the ranking: a direct "
        "order when a list price exists, a quote round otherwise",
        tags=["sourcing"],
    ),
]


def agent_spec(public_url: str) -> AgentSpec:
    return AgentSpec(
        name=AGENT_NAME,
        description="Sourcing and negotiation: quote rounds, comparisons, awards, "
        "counter-offers and alternative sources",
        version=__version__,
        url=public_url.rstrip("/") + "/a2a",
        skills=SKILLS,
    )


class SourcingHandler:
    def __init__(self, provider: AgentProvider) -> None:
        self._provider = provider

    async def handle(self, task_json: str, metadata: Mapping[str, Any]) -> AgentReply:
        try:
            task = SourcingTask.model_validate_json(task_json)
        except ValidationError as exc:
            logger.warning("invalid task: {}", exc.errors()[:3])
            return AgentReply(
                status="failed",
                text=json.dumps({"code": "invalid_task", "errors": exc.errors()}, default=str),
            )
        agent = await self._provider.get()
        result = await agent.run(task)
        return reply_for(result)


def reply_for(result: SourcingResult) -> AgentReply:
    status: ReplyStatus
    if result.status == "awaiting_approval":
        status = "input_required"
    elif result.status == "failed":
        status = "failed"
    else:
        status = "completed"
    return AgentReply(status=status, text=result.model_dump_json())
