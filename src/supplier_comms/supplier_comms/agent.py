"""Run and resume the graph; turn its state into a ``SupplierCommsResult``."""

from __future__ import annotations

from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from loguru import logger

from sc_core.graph import run_config
from sc_core.graph.approval import pending_for
from sc_core.infra import tracing
from sc_core.schema.a2a import (
    OutboundSummary,
    Outcome,
    SupplierCommsResult,
    SupplierCommsTask,
)
from sc_core.shared.errors import NotFound
from sc_core.shared.idempotency import new_id
from supplier_comms import AGENT_NAME
from supplier_comms.nodes.send import SEND_STEP


class SupplierCommsAgent:
    def __init__(self, graph: CompiledStateGraph, *, model: str) -> None:
        self._graph = graph
        self._model = model

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
        state = await self._graph.ainvoke(initial, run_config(task.case_id))
        return self.result_from(state)

    async def resume(self, case_id: str, decision: dict[str, Any]) -> SupplierCommsResult:
        """Continue a paused case with a decision. A finished case just returns its result."""
        config = run_config(case_id)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            raise NotFound(f"no run for case {case_id}", details={"case_id": case_id})
        if not snapshot.next:
            logger.bind(case_id=case_id).info("resume ignored: run already finished")
            return self.result_from(snapshot.values)
        state = await self._graph.ainvoke(Command(resume=decision), config)
        return self.result_from(state)

    @staticmethod
    def result_from(state: dict[str, Any]) -> SupplierCommsResult:
        task = SupplierCommsTask.model_validate(state["task"])
        interrupted = "__interrupt__" in state or not state.get("outcome")
        if state.get("outcome"):
            outcome = Outcome.model_validate(state["outcome"])
        elif interrupted:
            pending = pending_for(state, SEND_STEP) or _any_pending(state)
            outcome = Outcome(
                status="awaiting_approval",
                summary="esperando aprobación humana",
                approval_id=pending.get("approval_id") if pending else None,
            )
        else:  # pragma: no cover - defensive
            outcome = Outcome(status="failed", summary="run ended without an outcome")
        outbound = state.get("outbound")
        sent = state.get("sent") or {}
        return SupplierCommsResult(
            kind=task.kind,
            case_id=state["case_id"],
            run_id=state.get("run_id") or "run_unknown",
            outcome=outcome,
            po_name=task.po_name,
            outbound=OutboundSummary(
                kind=outbound["kind"],
                to=outbound["to"],
                subject=outbound["subject"],
                draft_id=outbound.get("draft_id"),
                sent_message_id=sent.get("sent_message_id"),
                web_link=sent.get("web_link") or outbound.get("web_link"),
            )
            if outbound
            else None,
            trace_id=state.get("trace_id"),
        )


def _any_pending(state: dict[str, Any]) -> dict[str, Any] | None:
    entries = state.get("pending_approvals") or []
    return dict(entries[-1]) if entries else None


__all__ = ["AGENT_NAME", "SupplierCommsAgent"]
