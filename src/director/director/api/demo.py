"""The Demo page's API: where the script stands, the next step, a reset.

Viewers follow the script; approvers drive it. ``next`` with ``approve`` decides
the approvals a step raises on the presenter's behalf (what ``scripts/demo_day.py
--auto`` uses); without it the presenter decides them in the inbox, live.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.approvals import (
    ApprovalsGateway,
    ResolveRequest,
    resolve_now,
)
from director.api.auth import Approver, Principal, Viewer
from director.autonomy import AutonomyChanges
from director.demo import DemoActor, DemoDirector, DemoView
from director.learning import FeedbackRecorder
from director.store import CaseStore
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError

router = APIRouter(prefix="/demo", tags=["demo"])


class DirectorApprovalResolver:
    """Approves through the same path as the inbox, so feedback and cases see it."""

    def __init__(
        self,
        gateway: ApprovalsGateway,
        cases: CaseStore,
        autonomy: AutonomyChanges,
        feedback: FeedbackRecorder,
    ) -> None:
        self._gateway = gateway
        self._cases = cases
        self._autonomy = autonomy
        self._feedback = feedback

    async def approve(self, approval_id: int, *, actor: DemoActor) -> None:
        principal = Principal(email=actor.email, name=actor.name, role="approver")
        try:
            await resolve_now(
                approval_id,
                ResolveRequest(status="approved", reason="demo"),
                principal,
                self._gateway,
                self._cases,
                self._autonomy,
                self._feedback,
            )
        except HTTPException as exc:
            raise ScError(f"approval {approval_id} could not be approved: {exc.detail}") from exc


class NextRequest(StrictModel):
    approve: bool = Field(
        default=False, description="decide the approvals this step raises, as the presenter"
    )
    step: str | None = Field(default=None, description="run this step instead of the next one")


@router.get("", response_model=DemoView)
async def read_demo(_: Principal = Viewer, demo: DemoDirector = Injected(DemoDirector)) -> DemoView:
    return await demo.view()


@router.post("/reset", response_model=DemoView)
async def reset_demo(
    principal: Principal = Approver, demo: DemoDirector = Injected(DemoDirector)
) -> DemoView:
    try:
        view = await demo.reset(DemoActor(name=principal.name, email=principal.email))
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    logger.bind(by=principal.email).info("demo reset from the Control Tower")
    return view


@router.post("/next", response_model=DemoView)
async def next_step(
    body: NextRequest,
    principal: Principal = Approver,
    demo: DemoDirector = Injected(DemoDirector),
) -> DemoView:
    actor = DemoActor(name=principal.name, email=principal.email)
    try:
        if body.step:
            return await demo.run(body.step, actor, approve=body.approve)
        return await demo.next(actor, approve=body.approve)
    except ScError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
