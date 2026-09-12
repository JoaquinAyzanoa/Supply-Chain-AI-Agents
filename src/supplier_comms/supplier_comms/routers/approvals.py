"""``POST /approvals/callback``: Odoo (or the Control Tower) resolved an approval.

The body is what ``sc.approval._sc_callback_payload`` produces, signed with
the approval's callback secret (our events secret). ``thread_id`` is the
case id of the paused graph. The endpoint is safe to retry: a case that
already finished returns its result with ``resumed=false``; a callback for
an approval the case is not waiting for is refused with 409.

The director is not on this hop (Odoo calls the agent directly), so after
a resume the agent publishes ``agent.run_finished`` to it in the background
and the case leaves ``awaiting_approval``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sc_core.a2a.events import EventPublisher, SignedBody
from sc_core.schema.a2a import SupplierCommsResult
from sc_core.schema.base import StrictModel
from sc_core.schema.events import AgentRunFinished, event_id_for
from supplier_comms import AGENT_NAME
from supplier_comms.service import AgentProvider

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalCallback(BaseModel):
    """Odoo's payload; extra keys are tolerated so the addon can grow without breaking us."""

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
    result: SupplierCommsResult


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
    result = await agent.resume(callback.thread_id, callback.decision())
    background.add_task(publisher.publish, run_finished(result, callback.approval_id))
    return CallbackResponse(resumed=True, result=result)


def run_finished(result: SupplierCommsResult, approval_id: int) -> AgentRunFinished:
    """The completion event for the director; deterministic id per run and outcome."""
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
        po_name=result.po_name,
        approval_id=approval_id,
        sent_message_id=result.outbound.sent_message_id if result.outbound else None,
    )
