"""Run and resume the graph; turn its state into a ``SourcingResult``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from loguru import logger

from sc_core.graph import run_config
from sc_core.graph.approval import pending_for
from sc_core.i18n import Language, t
from sc_core.infra import tracing
from sc_core.llm import RunBudget, current_budget
from sc_core.llm.budget import fresh_budget
from sc_core.schema.a2a import (
    CounterOffer,
    InvitedRfq,
    Outcome,
    QuoteComparison,
    SourcingResult,
    SourcingTask,
)
from sc_core.shared.errors import NotFound
from sc_core.shared.idempotency import new_id
from sourcing import AGENT_NAME
from sourcing.nodes.compare import AWARD_STEP
from sourcing.nodes.negotiate import OFFER_STEP
from sourcing.ports import SourcingPorts


class SourcingAgent:
    def __init__(
        self,
        graph: CompiledStateGraph,
        *,
        model: str,
        ports: SourcingPorts,
        language: Language = "en",
        budget: Callable[[], RunBudget] = fresh_budget,
    ) -> None:
        self._graph = graph
        self._model = model
        self._ports = ports
        self._language = language
        self._budget = budget

    async def run(self, task: SourcingTask) -> SourcingResult:
        run_id = new_id("run")
        initial: dict[str, Any] = {
            "task": task.model_dump(mode="json"),
            "case_id": task.case_id,
            "run_id": run_id,
            "model": self._model,
            "trace_id": tracing.current_trace_id(),
        }
        logger.bind(case_id=task.case_id, run_id=run_id, kind=task.kind).info("run started")
        budget = self._budget()
        token = current_budget.set(budget)
        try:
            state = await self._graph.ainvoke(initial, run_config(task.case_id))
        except Exception as exc:
            await self._fail(run_id, exc, budget)
            raise
        finally:
            current_budget.reset(token)
        return await self._finish(state, budget)

    async def resume(self, case_id: str, decision: dict[str, Any]) -> SourcingResult:
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
        message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
        try:
            await self._ports.finish_run(
                run_id, status="failed", summary=str(message)[:500], usage=budget.snapshot()
            )
        except Exception as inner:  # the log must never hide the original failure
            logger.bind(run_id=run_id).warning("could not mark the run failed: {}", inner)

    async def pending_approval_id(self, case_id: str) -> int | None:
        snapshot = await self._graph.aget_state(run_config(case_id))
        if not snapshot.values or not snapshot.next:
            return None
        pending = _any_pending(snapshot.values)
        return int(pending["approval_id"]) if pending else None

    async def _finish(self, state: dict[str, Any], budget: RunBudget) -> SourcingResult:
        result = self.result_from(state)
        await self._ports.finish_run(
            result.run_id,
            status=result.status,
            summary=result.outcome.summary,
            usage=budget.snapshot(),
        )
        # the round remembers which approval it waits for, so the Control Tower can show it
        pending = pending_for(state, AWARD_STEP)
        if result.status == "awaiting_approval" and pending and result.round_id:
            await self._ports.update_round(
                result.round_id,
                status="awaiting_award",
                award_approval_id=pending.get("approval_id"),
            )
        logger.bind(case_id=result.case_id, run_id=result.run_id, status=result.status).info(
            "run {}", "paused" if result.status == "awaiting_approval" else "finished"
        )
        return result

    async def snapshot(self, case_id: str) -> dict[str, Any]:
        return dict((await self._graph.aget_state(run_config(case_id))).values)

    def result_from(self, state: dict[str, Any]) -> SourcingResult:
        return result_from(state, language=self._language)


def result_from(state: dict[str, Any], *, language: Language = "en") -> SourcingResult:
    task = SourcingTask.model_validate(state["task"])
    if state.get("outcome"):
        outcome = Outcome.model_validate(state["outcome"])
        if outcome.approval_id is None and state.get("escalation_approval_id"):
            outcome = outcome.model_copy(update={"approval_id": state["escalation_approval_id"]})
    else:
        pending = (
            pending_for(state, AWARD_STEP) or pending_for(state, OFFER_STEP) or _any_pending(state)
        )
        outcome = Outcome(
            status="awaiting_approval",
            summary=t("common.awaiting_approval", language),
            approval_id=pending.get("approval_id") if pending else None,
        )
    round_ = state.get("round") or {}
    comparison = state.get("comparison")
    offer = state.get("offer")
    return SourcingResult(
        kind=task.kind,
        case_id=state["case_id"],
        run_id=state.get("run_id") or "run_unknown",
        outcome=outcome,
        round_id=round_.get("id") if round_ else None,
        invited=[InvitedRfq.model_validate(i) for i in state.get("invited") or []],
        comparison=QuoteComparison.model_validate(comparison) if comparison else None,
        counter_offer=CounterOffer.model_validate(offer) if offer else None,
        awarded_po_name=state.get("awarded_po_name"),
        trace_id=state.get("trace_id"),
    )


def _any_pending(state: dict[str, Any]) -> dict[str, Any] | None:
    entries = state.get("pending_approvals") or []
    return dict(entries[-1]) if entries else None


__all__ = ["AGENT_NAME", "SourcingAgent", "result_from"]
