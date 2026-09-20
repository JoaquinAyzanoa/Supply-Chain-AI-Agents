"""``POST /approvals/callback``: a person decided on an award or a counter-offer."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sc_core.a2a.events import EventPublisher, SignedBody
from sc_core.schema.a2a import SourcingResult
from sc_core.schema.base import StrictModel
from sc_core.schema.events import AgentRunFinished, event_id_for
from sourcing import AGENT_NAME
from sourcing.infra.service import AgentProvider

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalCallback(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_id: int
    status: Literal["approved", "rejected", "expired"]
    thread_id: str = Field(min_length=1)
    kind: str | None = None
    resolved_by: str | None = None
    resolved_by_name: str | None = None
    reason: str | None = None
    details: dict[str, Any] | None = None

    def decision(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "status": self.status,
            "resolved_by": self.resolved_by_name or self.resolved_by or None,
            "reason": self.reason or None,
            "details": self.details or None,
        }


class CallbackResponse(StrictModel):
    resumed: bool
    result: SourcingResult


@router.post("/callback", response_model=CallbackResponse)
async def approval_callback(
    background: BackgroundTasks,
    body: bytes = SignedBody,
    provider: AgentProvider = Injected(AgentProvider),
    publisher: EventPublisher = Injected(EventPublisher),
) -> CallbackResponse:
    try:
        callback = ApprovalCallback.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    agent = await provider.get()
    pending = await agent.pending_approval_id(callback.thread_id)
    if pending is None:
        snapshot = await agent.snapshot(callback.thread_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail=f"unknown case {callback.thread_id}")
        logger.bind(case_id=callback.thread_id, approval_id=callback.approval_id).info(
            "callback for a finished case; nothing to resume"
        )
        return CallbackResponse(resumed=False, result=agent.result_from(snapshot))
    if pending != callback.approval_id:
        raise HTTPException(
            status_code=409,
            detail=f"case {callback.thread_id} waits for approval {pending}, "
            f"not {callback.approval_id}",
        )
    # What follows an award or a counter-offer takes longer than Odoo waits for the
    # callback (confirmations, declines and emails through the supplier agent): the run
    # resumes in the background and the director learns the outcome from the event.
    snapshot = await agent.snapshot(callback.thread_id)
    background.add_task(_resume, agent, callback, publisher)
    return CallbackResponse(resumed=True, result=agent.result_from(snapshot))


async def _resume(agent: Any, callback: ApprovalCallback, publisher: EventPublisher) -> None:
    try:
        result = await agent.resume(callback.thread_id, callback.decision())
    except Exception as exc:  # the failure is on the run row; nothing else can hear it here
        logger.bind(case_id=callback.thread_id, approval_id=callback.approval_id).error(
            "resume after the decision failed: {}", exc
        )
        return
    await publisher.publish(run_finished(result, callback.approval_id))


def run_finished(result: SourcingResult, approval_id: int) -> AgentRunFinished:
    return AgentRunFinished(
        event_id=event_id_for("agent.run_finished", result.run_id, result.status),
        source=AGENT_NAME,
        case_id=result.case_id,
        trace_id=result.trace_id,
        agent=AGENT_NAME,
        thread_id=result.case_id,
        run_id=result.run_id,
        task_kind=result.kind,
        status=result.status,
        summary=result.outcome.summary,
        approval_id=approval_id,
    )
