"""Run and resume the planning graph; turn its state into an ``InventoryPlanningResult``."""

from __future__ import annotations

from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from loguru import logger

from inventory_planning import AGENT_NAME
from inventory_planning.nodes.apply import PLAN_STEP
from inventory_planning.ports import WritePorts
from sc_core.graph import run_config
from sc_core.graph.approval import pending_for
from sc_core.i18n import Language, t
from sc_core.infra import tracing
from sc_core.schema.a2a import (
    AppliedSummary,
    InventoryPlanningResult,
    InventoryPlanningTask,
    Outcome,
)
from sc_core.schema.planning import ReplenishmentProposal
from sc_core.shared.errors import NotFound
from sc_core.shared.idempotency import new_id


class InventoryPlanningAgent:
    def __init__(
        self,
        graph: CompiledStateGraph,
        *,
        model: str,
        writes: WritePorts,
        language: Language = "en",
    ) -> None:
        self._graph = graph
        self._model = model
        self._writes = writes
        self._language = language

    async def run(self, task: InventoryPlanningTask) -> InventoryPlanningResult:
        run_id = new_id("run")
        initial: dict[str, Any] = {
            "task": task.model_dump(mode="json"),
            "case_id": task.case_id,
            "run_id": run_id,
            "model": self._model,
            "trace_id": tracing.current_trace_id(),
        }
        await self._writes.start_run(
            run_id=run_id,
            agent=AGENT_NAME,
            case_id=task.case_id,
            model=self._model,
            trace_url=tracing.trace_url(tracing.current_trace_id()),
        )
        logger.bind(case_id=task.case_id, run_id=run_id, kind=task.kind).info("run started")
        state = await self._graph.ainvoke(initial, run_config(task.case_id))
        return await self._finish(state)

    async def resume(self, case_id: str, decision: dict[str, Any]) -> InventoryPlanningResult:
        config = run_config(case_id)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            raise NotFound(f"no run for case {case_id}", details={"case_id": case_id})
        if not snapshot.next:
            logger.bind(case_id=case_id).info("resume ignored: run already finished")
            return self.result_from(snapshot.values)
        state = await self._graph.ainvoke(Command(resume=decision), config)
        return await self._finish(state)

    async def pending_approval_id(self, case_id: str) -> int | None:
        snapshot = await self._graph.aget_state(run_config(case_id))
        if not snapshot.values or not snapshot.next:
            return None
        pending = pending_for(snapshot.values, PLAN_STEP)
        return int(pending["approval_id"]) if pending else None

    async def snapshot(self, case_id: str) -> dict[str, Any]:
        return dict((await self._graph.aget_state(run_config(case_id))).values)

    async def _finish(self, state: dict[str, Any]) -> InventoryPlanningResult:
        result = self.result_from(state)
        await self._writes.finish_run(
            result.run_id, status=result.status, summary=result.outcome.summary
        )
        logger.bind(case_id=result.case_id, run_id=result.run_id, status=result.status).info(
            "run {}", "paused" if result.status == "awaiting_approval" else "finished"
        )
        return result

    def result_from(self, state: dict[str, Any]) -> InventoryPlanningResult:
        return result_from(state, language=self._language)


def result_from(state: dict[str, Any], *, language: Language = "en") -> InventoryPlanningResult:
    """The result a run's state describes; the waiting summary is in ``language``."""
    if True:  # kept flat to leave the original body untouched
        task = InventoryPlanningTask.model_validate(state["task"])
        if state.get("outcome"):
            outcome = Outcome.model_validate(state["outcome"])
        else:
            pending = pending_for(state, PLAN_STEP)
            outcome = Outcome(
                status="awaiting_approval",
                summary=t("plan.awaiting", language),
                approval_id=pending.get("approval_id") if pending else None,
            )
        proposal = state.get("proposal")
        applied = state.get("applied")
        return InventoryPlanningResult(
            kind=task.kind,
            case_id=state["case_id"],
            run_id=state.get("run_id") or "run_unknown",
            outcome=outcome,
            proposal=ReplenishmentProposal.model_validate(proposal) if proposal else None,
            applied=AppliedSummary.model_validate(applied) if applied else None,
            trace_id=state.get("trace_id"),
        )
