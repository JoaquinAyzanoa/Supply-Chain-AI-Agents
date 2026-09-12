"""``POST /approvals/callback``: Odoo (or the Control Tower) resolved an approval.

The body is what ``sc.approval._sc_callback_payload`` produces, signed with
the approval's callback secret (our events secret). ``thread_id`` is the
case id of the paused graph. The endpoint is safe to retry: a case that
already finished returns its result with ``resumed=false``; a callback for
an approval the case is not waiting for is refused with 409.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sc_core.a2a.events import SignedBody
from sc_core.schema.a2a import SupplierCommsResult
from sc_core.schema.base import StrictModel
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
    reason: str | None = None

    def decision(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "status": self.status,
            "resolved_by": self.resolved_by or None,
            "reason": self.reason or None,
        }


class CallbackResponse(StrictModel):
    resumed: bool
    result: SupplierCommsResult


@router.post("/callback", response_model=CallbackResponse)
async def approval_callback(
    body: bytes = SignedBody, provider: AgentProvider = Injected(AgentProvider)
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
    return CallbackResponse(resumed=True, result=result)
