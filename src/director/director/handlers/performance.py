"""The weekly supplier performance job: one task to the performance agent, on its own case."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from loguru import logger

from director.agents import Agents
from director.escalation import Escalator
from director.store import CaseStore
from director.workflow import (
    ConversationLookup,
    NoConversations,
    consolidate_outcome,
    outcome_from_reply,
)
from sc_core.schema.a2a import SupplierPerformanceTask
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import ScError
from sc_core.shared.time import local_today

AGENT = "supplier_performance"


class PerformanceJob:
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
        if job_id != "supplier_performance":
            return {"job": job_id, "status": "not_implemented"}
        today = self._today()
        thread_id = f"scores_{today.isoformat()}_{tick.run_id}"
        case, _ = await self._cases.attach_or_create(kind="receipt", po_name=None, agent=AGENT)
        task = SupplierPerformanceTask(kind="weekly_scorecard", case_id=thread_id, as_of=today)
        await self._cases.add_event(
            case.case_id,
            "task_sent",
            {"agent": AGENT, "task": task.kind, "thread_id": thread_id, "run_id": tick.run_id},
        )
        try:
            reply = await self._agents.for_name(AGENT).send(
                task.model_dump_json(), case_id=case.case_id
            )
        except (ScError, LookupError) as exc:
            error = exc if isinstance(exc, ScError) else ScError(str(exc))
            outcome = outcome_from_reply(case, AGENT, task.kind, thread_id, None, error=error)
        else:
            outcome = outcome_from_reply(case, AGENT, task.kind, thread_id, reply)
        update = await consolidate_outcome(
            self._cases, self._escalator, self._conversations, outcome
        )
        logger.bind(run_id=tick.run_id, case_id=case.case_id, status=update.status).info(
            "weekly scorecard dispatched"
        )
        return {
            "job": job_id,
            "status": "ok",
            "case_id": case.case_id,
            "scorecard_status": outcome.status,
            "summary": outcome.summary,
            "approval_id": outcome.approval_id,
        }
