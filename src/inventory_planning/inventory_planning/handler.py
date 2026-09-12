"""A2A entry point: a task in, a result out.

The task JSON is validated before anything runs; an invalid task is a
``failed`` reply without a graph call. A run paused on the plan's approval
answers ``input_required`` so the caller knows a person is in the loop.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from loguru import logger
from pydantic import ValidationError

from inventory_planning import AGENT_NAME, __version__
from inventory_planning.service import AgentProvider
from sc_core.a2a import AgentReply, AgentSpec, Skill
from sc_core.a2a.protocol import ReplyStatus
from sc_core.schema.a2a import InventoryPlanningResult, InventoryPlanningTask

SKILLS = [
    Skill(
        id="daily_plan",
        name="Daily plan",
        description="Forecast every product, compute reorder rules and order quantities, "
        "explain exceptions and propose the run for approval",
        tags=["planning"],
    ),
    Skill(
        id="review_product",
        name="Review product",
        description="Re-plan one product with text context (notes, a reorder trigger)",
        tags=["planning"],
    ),
    Skill(
        id="what_if",
        name="What if",
        description="Simulate parameters for one product; no approval, no writes",
        tags=["planning", "simulation"],
    ),
]


def agent_spec(public_url: str) -> AgentSpec:
    return AgentSpec(
        name=AGENT_NAME,
        description="Inventory planning: statistical forecasts, replenishment policy, "
        "explained exceptions, reorder rules and draft RFQs with human approval",
        version=__version__,
        url=public_url.rstrip("/") + "/a2a",
        skills=SKILLS,
    )


class InventoryPlanningHandler:
    def __init__(self, provider: AgentProvider) -> None:
        self._provider = provider

    async def handle(self, task_json: str, metadata: Mapping[str, Any]) -> AgentReply:
        try:
            task = InventoryPlanningTask.model_validate_json(task_json)
        except ValidationError as exc:
            logger.warning("invalid task: {}", exc.errors()[:3])
            return AgentReply(
                status="failed",
                text=json.dumps({"code": "invalid_task", "errors": exc.errors()}, default=str),
            )
        agent = await self._provider.get()
        result = await agent.run(task)
        return reply_for(result)


def reply_for(result: InventoryPlanningResult) -> AgentReply:
    status: ReplyStatus
    if result.status == "awaiting_approval":
        status = "input_required"
    elif result.status == "failed":
        status = "failed"
    else:
        status = "completed"
    return AgentReply(status=status, text=result.model_dump_json())
