"""The autonomy policy for the Control Tower: read it, test it, preview it on the
last weeks of approvals, change it, and see or revert what ran alone.

``PUT`` saves at once when the edit only lowers autonomy; when it widens it,
an ``autonomy_change`` approval is created and a second person confirms it
(the approvals API refuses the requester). ``GET /actions`` is the "done
automatically" feed; ``POST /actions/{id}/revert`` applies the inverse write
while the row's window is open.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.approvals import ApprovalsGateway
from director.api.auth import Admin, Approver, Principal, Viewer
from director.api.settings import RuntimeSettingsStore, SettingsVersion
from director.autonomy import (
    AutoAction,
    AutoActionsStore,
    AutonomyChanges,
    Reverter,
    since_days,
    window_open,
)
from director.store import CaseStore
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.odoo.repositories import ApprovalRepo
from sc_core.schema.autonomy import ActionFacts, AutonomyPolicy, PolicyDecision
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError, ValidationFailed

router = APIRouter(prefix="/autonomy", tags=["autonomy"])


class PendingChange(StrictModel):
    approval_id: int
    requested_by: str
    widened: list[str]


class PolicyView(StrictModel):
    version: int
    changed_by: str
    changed_at: datetime | None = None
    policy: AutonomyPolicy
    pending: PendingChange | None = None


class DecideRequest(StrictModel):
    kind: str
    facts: ActionFacts = Field(default_factory=ActionFacts)
    policy: AutonomyPolicy | None = None  # a draft; the current policy when omitted


class PreviewRequest(StrictModel):
    policy: AutonomyPolicy
    days: int = Field(default=30, ge=1, le=365)


class PreviewRow(StrictModel):
    approval_id: int
    kind: str
    summary: str
    created_at: datetime | None = None
    status: str
    level: str
    rule_id: str | None = None


class PreviewResponse(StrictModel):
    days: int
    total: int
    would_run_alone: int
    by_rule: dict[str, int]
    by_kind: dict[str, dict[str, int]]
    rows: list[PreviewRow]


class PolicyUpdate(StrictModel):
    policy: AutonomyPolicy
    note: str | None = Field(default=None, max_length=300)


class PolicyUpdateResponse(StrictModel):
    saved: SettingsVersion | None = None
    approval_id: int | None = None
    widened: list[str] = []


class AutoActionView(StrictModel):
    id: int
    created_at: datetime
    case_id: str | None = None
    run_id: str | None = None
    agent: str
    kind: str
    level: str
    rule_id: str | None = None
    summary: str
    po_id: int | None = None
    po_name: str | None = None
    partner_id: int | None = None
    payload: dict[str, Any] = {}
    revertible: bool
    revert_until: datetime | None = None
    reverted_at: datetime | None = None
    reverted_by: str | None = None


class RevertResponse(StrictModel):
    id: int
    reverted_at: datetime
    note: str


def action_view(action: AutoAction, *, now: datetime | None = None) -> AutoActionView:
    return AutoActionView(
        **action.model_dump(exclude={"revert"}),
        revertible=window_open(action, now=now),
    )


async def _pending_change(approvals: ApprovalsGateway) -> PendingChange | None:
    rows = await approvals.list(status="pending", kind="autonomy_change", po_name=None)
    if not rows:
        return None
    payload = ApprovalRepo.payload_of(rows[0])
    return PendingChange(
        approval_id=rows[0].id,
        requested_by=str(payload.get("requested_by") or "-"),
        widened=[str(x) for x in payload.get("widened") or []],
    )


@router.get("", response_model=PolicyView)
async def get_policy(
    _: Principal = Viewer,
    store: RuntimeSettingsStore = Injected(RuntimeSettingsStore),  # type: ignore[type-abstract]
    reader: RuntimeSettingsReader = Injected(RuntimeSettingsReader),
    approvals: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
) -> PolicyView:
    current = await store.current()
    settings = current.settings if current else reader.defaults
    return PolicyView(
        version=current.version if current else 0,
        changed_by=current.changed_by if current else "environment",
        changed_at=current.changed_at if current else None,
        policy=settings.autonomy,
        pending=await _pending_change(approvals),
    )


@router.post("/decide", response_model=PolicyDecision)
async def decide(
    body: DecideRequest,
    _: Principal = Viewer,
    changes: AutonomyChanges = Injected(AutonomyChanges),
) -> PolicyDecision:
    """What the policy (current, or the draft given) says about one action."""
    policy = body.policy or (await changes.current_settings()).autonomy
    return policy.decide(body.kind, body.facts)


@router.post("/preview", response_model=PreviewResponse)
async def preview(
    body: PreviewRequest,
    _: Principal = Viewer,
    approvals: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
) -> PreviewResponse:
    """Replay the last ``days`` of approvals against a draft policy: how many would
    have run alone, by rule and by kind, and the list."""
    since = since_days(body.days)
    rows = await approvals.list(status="all", kind=None, po_name=None)
    out: list[PreviewRow] = []
    by_rule: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    for approval in rows:
        if approval.create_date is not None and approval.create_date < since:
            continue
        if approval.kind == "autonomy_change":
            continue
        payload = ApprovalRepo.payload_of(approval)
        facts = ActionFacts.model_validate(payload.get("facts") or {})
        verdict = body.policy.decide(approval.kind, facts)
        out.append(
            PreviewRow(
                approval_id=approval.id,
                kind=approval.kind,
                summary=approval.summary,
                created_at=approval.create_date,
                status=approval.status,
                level=verdict.level,
                rule_id=verdict.rule_id,
            )
        )
        if verdict.rule_id:
            by_rule[verdict.rule_id] = by_rule.get(verdict.rule_id, 0) + 1
        kind_counts = by_kind.setdefault(approval.kind, {})
        kind_counts[verdict.level] = kind_counts.get(verdict.level, 0) + 1
    alone = sum(1 for r in out if r.level != "approve")
    return PreviewResponse(
        days=body.days,
        total=len(out),
        would_run_alone=alone,
        by_rule=by_rule,
        by_kind=by_kind,
        rows=out[:200],
    )


@router.put("", response_model=PolicyUpdateResponse)
async def put_policy(
    body: PolicyUpdate,
    principal: Principal = Admin,
    changes: AutonomyChanges = Injected(AutonomyChanges),
    approvals: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
) -> PolicyUpdateResponse:
    current = (await changes.current_settings()).autonomy
    widened = body.policy.raises_over(current)
    if not widened:
        saved = await changes.save(body.policy, changed_by=principal.email, note=body.note)
        return PolicyUpdateResponse(saved=saved, widened=[])
    if await _pending_change(approvals) is not None:
        raise HTTPException(
            status_code=409, detail="an autonomy change is already waiting for a second person"
        )
    try:
        approval = await changes.request(
            body.policy,
            widened=widened,
            requested_by=principal.email,
            requested_by_name=principal.name,
            note=body.note,
        )
    except ScError as exc:
        raise HTTPException(status_code=502, detail=f"Odoo refused: {exc.message}") from exc
    logger.bind(approval_id=approval.id, by=principal.email, widened=widened).info(
        "autonomy change needs a second person"
    )
    return PolicyUpdateResponse(approval_id=approval.id, widened=widened)


@router.get("/actions", response_model=list[AutoActionView])
async def list_actions(
    days: int = Query(default=7, ge=1, le=90),
    _: Principal = Viewer,
    store: AutoActionsStore = Injected(AutoActionsStore),  # type: ignore[type-abstract]
) -> list[AutoActionView]:
    """The "done automatically" feed, newest first."""
    now = datetime.now(UTC)
    rows = await store.recent(since=since_days(days))
    return [action_view(row, now=now) for row in rows]


@router.post("/actions/{action_id}/revert", response_model=RevertResponse)
async def revert_action(
    action_id: int,
    principal: Principal = Approver,
    store: AutoActionsStore = Injected(AutoActionsStore),  # type: ignore[type-abstract]
    reverter: Reverter = Injected(Reverter),  # type: ignore[type-abstract]
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
) -> RevertResponse:
    action = await store.get(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail=f"automatic action {action_id} not found")
    if action.revert is None:
        raise HTTPException(
            status_code=422, detail=f"an automatic {action.kind} cannot be reverted"
        )
    if action.reverted_at is not None:
        raise HTTPException(status_code=409, detail="already reverted")
    if not window_open(action):
        raise HTTPException(status_code=409, detail="the revert window is closed")
    try:
        note = await reverter.revert(action, by=principal.name)
    except ValidationFailed as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except ScError as exc:
        raise HTTPException(status_code=502, detail=f"Odoo refused: {exc.message}") from exc
    updated = await store.mark_reverted(action_id, by=principal.email)
    if action.case_id and await cases.get(action.case_id) is not None:
        await cases.add_event(
            action.case_id,
            "note",
            {"text": note, "auto_action_id": action_id, "by": principal.email},
        )
    logger.bind(action_id=action_id, by=principal.email).info("automatic action reverted")
    return RevertResponse(
        id=action_id, reverted_at=updated.reverted_at or datetime.now(UTC), note=note
    )
