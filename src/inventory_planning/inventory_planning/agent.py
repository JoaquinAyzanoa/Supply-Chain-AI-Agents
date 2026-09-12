"""Run and resume the planning graph; turn its state into an ``InventoryPlanningResult``."""

from __future__ import annotations

from collections.abc import Callable
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
from sc_core.llm import RunBudget, current_budget
from sc_core.llm.budget import fresh_budget
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
        budget: Callable[[], RunBudget] = fresh_budget,
    ) -> None:
        self._graph = graph
        self._model = model
        self._writes = writes
        self._language = language
        self._budget = budget

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
        budget = self._budget()
        token = current_budget.set(budget)  # every model call in this run counts against it
        try:
            state = await self._graph.ainvoke(initial, run_config(task.case_id))
        except Exception as exc:
            await self._fail(run_id, exc, budget)
            raise
        finally:
            current_budget.reset(token)
        return await self._finish(state, budget)

    async def resume(self, case_id: str, decision: dict[str, Any]) -> InventoryPlanningResult:
        config = run_config(case_id)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            raise NotFound(f"no run for case {case_id}", details={"case_id": case_id})
        if not snapshot.next:
            logger.bind(case_id=case_id).info("resume ignored: run already finished")
            return self.result_from(snapshot.values)
        budget = self._budget()
        token = current_budget.set(budget)
        try:
            state = await self._graph.ainvoke(Command(resume=decision), config)
        except Exception as exc:
            await self._fail(str(snapshot.values.get("run_id") or case_id), exc, budget)
            raise
        finally:
            current_budget.reset(token)
        return await self._finish(state, budget)

    async def _fail(self, run_id: str, exc: Exception, budget: RunBudget) -> None:
        """Close the run log as failed; the error itself still propagates to the caller."""
        message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
        try:
            await self._writes.finish_run(
                run_id, status="failed", summary=str(message)[:500], usage=budget.snapshot()
            )
        except Exception as inner:  # the log must never hide the original failure
            logger.bind(run_id=run_id).warning("could not mark the run failed: {}", inner)

    async def pending_approval_id(self, case_id: str) -> int | None:
        snapshot = await self._graph.aget_state(run_config(case_id))
        if not snapshot.values or not snapshot.next:
            return None
        pending = pending_for(snapshot.values, PLAN_STEP)
        return int(pending["approval_id"]) if pending else None

    async def snapshot(self, case_id: str) -> dict[str, Any]:
        return dict((await self._graph.aget_state(run_config(case_id))).values)

    async def _finish(self, state: dict[str, Any], budget: RunBudget) -> InventoryPlanningResult:
        result = self.result_from(state)
        await self._writes.finish_run(
            result.run_id,
            status=result.status,
            summary=result.outcome.summary,
            usage=budget.snapshot(),
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
