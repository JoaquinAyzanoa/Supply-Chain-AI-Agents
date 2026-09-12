"""The daily planning job: one ``daily_plan`` task to the inventory planning agent.

The scheduler's ``inventory_planning`` tick becomes a planning case; the
agent proposes the run and pauses on its approval, and the case follows the
same consolidation as any other agent outcome (``agent.run_finished`` closes
it after the person decides).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from loguru import logger

from director.agents import Agents
from director.escalation import Escalator
from director.jobs import JobRunner
from director.store import CaseStore
from director.workflow import (
    ConversationLookup,
    NoConversations,
    consolidate_outcome,
    outcome_from_reply,
)
from sc_core.schema.a2a import InventoryPlanningTask
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import ScError
from sc_core.shared.time import local_today

AGENT = "inventory_planning"


class PlanningJob:
    def __init__(
        self,
        *,
        cases: CaseStore,
        agents: Agents,
        escalator: Escalator,
        conversations: ConversationLookup | None = None,
        today: Callable[[], date] = local_today,
    ) -> None:
        self._cases = cases
        self._agents = agents
        self._escalator = escalator
        self._conversations = conversations or NoConversations()
        self._today = today

    async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]:
        if job_id != "inventory_planning":
            return {"job": job_id, "status": "not_implemented"}
        today = self._today()
        thread_id = f"plan_{today.isoformat()}_{tick.run_id}"
        case, _ = await self._cases.attach_or_create(kind="planning", po_name=None, agent=AGENT)
        task = InventoryPlanningTask(kind="daily_plan", case_id=thread_id, as_of=today)
        await self._cases.add_event(
            case.case_id,
            "task_sent",
            {"agent": AGENT, "task": task.kind, "thread_id": thread_id, "run_id": tick.run_id},
        )
        try:
            reply = await self._agents.for_name(AGENT).send(
                task.model_dump_json(), case_id=case.case_id
            )
        except ScError as exc:
            outcome = outcome_from_reply(case, AGENT, task.kind, thread_id, None, error=exc)
        else:
            outcome = outcome_from_reply(case, AGENT, task.kind, thread_id, reply)
        update = await consolidate_outcome(
            self._cases, self._escalator, self._conversations, outcome
        )
        logger.bind(run_id=tick.run_id, case_id=case.case_id, status=update.status).info(
            "daily plan dispatched"
        )
        return {
            "job": job_id,
            "status": "ok",
            "case_id": case.case_id,
            "plan_status": outcome.status,
            "summary": outcome.summary,
            "approval_id": outcome.approval_id,
        }


class JobDispatcher:
    """One ``JobRunner`` per job id; unknown ids are recorded as not implemented."""

    def __init__(self, runners: dict[str, JobRunner]) -> None:
        self._runners = runners

    async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]:
        runner = self._runners.get(job_id)
        if runner is None:
            logger.bind(job=job_id, run_id=tick.run_id).info("job not implemented yet")
            return {"job": job_id, "status": "not_implemented"}
        return await runner.run(job_id, tick)
