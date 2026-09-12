"""Run and resume the graph; turn its state into a ``SupplierCommsResult``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from loguru import logger
from pydantic import BaseModel

from sc_core.graph import run_config
from sc_core.graph.approval import pending_for
from sc_core.i18n import Language, t
from sc_core.infra import tracing
from sc_core.llm import RunBudget, current_budget
from sc_core.llm.budget import fresh_budget
from sc_core.schema.a2a import (
    ChangeProposal,
    Classification,
    OutboundSummary,
    Outcome,
    QuotationData,
    SupplierCommsResult,
    SupplierCommsTask,
)
from sc_core.shared.errors import NotFound
from sc_core.shared.idempotency import new_id
from supplier_comms import AGENT_NAME
from supplier_comms.nodes.apply import CHANGE_STEP
from supplier_comms.nodes.send import SEND_STEP
from supplier_comms.ports import AgentPorts


class SupplierCommsAgent:
    def __init__(
        self,
        graph: CompiledStateGraph,
        *,
        model: str,
        ports: AgentPorts,
        language: Language = "en",
        budget: Callable[[], RunBudget] = fresh_budget,
    ) -> None:
        self._graph = graph
        self._model = model
        self._ports = ports
        self._language = language
        self._budget = budget

    async def run(self, task: SupplierCommsTask) -> SupplierCommsResult:
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
        token = current_budget.set(budget)  # every model call in this run counts against it
        try:
            state = await self._graph.ainvoke(initial, run_config(task.case_id))
        except Exception as exc:
            await self._fail(run_id, exc, budget)
            raise
        finally:
            current_budget.reset(token)
        return await self._finish(state, budget)

    async def resume(self, case_id: str, decision: dict[str, Any]) -> SupplierCommsResult:
        """Continue a paused case with a decision. A finished case just returns its result."""
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
            await self._ports.finish_run(
                run_id, status="failed", summary=str(message)[:500], usage=budget.snapshot()
            )
        except Exception as inner:  # the log must never hide the original failure
            logger.bind(run_id=run_id).warning("could not mark the run failed: {}", inner)

    async def pending_approval_id(self, case_id: str) -> int | None:
        """The approval a paused case is waiting for; ``None`` when finished or unknown."""
        snapshot = await self._graph.aget_state(run_config(case_id))
        if not snapshot.values or not snapshot.next:
            return None
        pending = _any_pending(snapshot.values)
        return int(pending["approval_id"]) if pending else None

    async def _finish(self, state: dict[str, Any], budget: RunBudget) -> SupplierCommsResult:
        result = self.result_from(state)
        await self._ports.finish_run(
            result.run_id,
            status=result.status,
            summary=result.outcome.summary,
            usage=budget.snapshot(),
        )
        logger.bind(case_id=result.case_id, run_id=result.run_id, status=result.status).info(
            "run {}", "paused" if result.status == "awaiting_approval" else "finished"
        )
        return result

    async def snapshot(self, case_id: str) -> dict[str, Any]:
        """The persisted state of a case (tests and the callback use it)."""
        return dict((await self._graph.aget_state(run_config(case_id))).values)

    def result_from(self, state: dict[str, Any]) -> SupplierCommsResult:
        return result_from(state, language=self._language)


def result_from(state: dict[str, Any], *, language: Language = "en") -> SupplierCommsResult:
    """The result a run's state describes; the waiting summary is in ``language``."""
    if True:  # kept flat to leave the original body untouched
        task = SupplierCommsTask.model_validate(state["task"])
        if state.get("outcome"):
            outcome = Outcome.model_validate(state["outcome"])
            if outcome.approval_id is None and state.get("escalation_approval_id"):
                outcome = outcome.model_copy(
                    update={"approval_id": state["escalation_approval_id"]}
                )
        else:
            pending = (
                pending_for(state, SEND_STEP)
                or pending_for(state, CHANGE_STEP)
                or _any_pending(state)
            )
            outcome = Outcome(
                status="awaiting_approval",
                summary=t("common.awaiting_approval", language),
                approval_id=pending.get("approval_id") if pending else None,
            )
        outbound = state.get("outbound")
        sent = state.get("sent") or {}
        return SupplierCommsResult(
            kind=task.kind,
            case_id=state["case_id"],
            run_id=state.get("run_id") or "run_unknown",
            outcome=outcome,
            po_name=task.po_name,
            chosen_po_name=state.get("chosen_po_name"),
            classification=_model(Classification, state.get("classification")),
            extracted=_model(QuotationData, state.get("extracted")),
            proposal=_model(ChangeProposal, state.get("proposal")),
            outbound=OutboundSummary(
                kind=outbound["kind"],
                to=outbound["to"],
                subject=outbound["subject"],
                attachments=list(outbound.get("attachments") or []),
                draft_id=outbound.get("draft_id"),
                sent_message_id=sent.get("sent_message_id"),
                web_link=sent.get("web_link") or outbound.get("web_link"),
            )
            if outbound
            else None,
            trace_id=state.get("trace_id"),
        )


def _model[M: BaseModel](cls: type[M], data: dict[str, Any] | None) -> M | None:
    return cls.model_validate(data) if data else None


def _any_pending(state: dict[str, Any]) -> dict[str, Any] | None:
    entries = state.get("pending_approvals") or []
    return dict(entries[-1]) if entries else None


__all__ = ["AGENT_NAME", "SupplierCommsAgent"]
