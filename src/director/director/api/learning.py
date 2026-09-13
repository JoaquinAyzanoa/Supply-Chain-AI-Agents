"""What the system learned from people: decision statistics, calibration suggestions
and supplier profiles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger

from director.api.approvals import ApprovalsGateway
from director.api.auth import Admin, Approver, Principal, Viewer
from director.api.autonomy import _pending_change
from director.autonomy import AutonomyChanges
from director.learning import (
    FeedbackStats,
    FeedbackStore,
    Suggestion,
    SuggestionStore,
    group_stats,
)
from sc_core.infra.profiles import ProfileStore
from sc_core.schema.autonomy import AutonomyPolicy, AutonomyRule
from sc_core.schema.base import StrictModel
from sc_core.schema.profiles import SupplierProfile
from sc_core.shared.errors import NotFound, ScError

router = APIRouter(prefix="/learning", tags=["learning"])


class FeedbackSummary(StrictModel):
    days: int
    total: int
    unchanged: int
    edited: int
    rejected: int
    expired: int
    by_kind: list[FeedbackStats]
    by_supplier: list[FeedbackStats]


class SuggestionAction(StrictModel):
    suggestion: Suggestion
    saved_version: int | None = None
    approval_id: int | None = None


class ProfileUpdate(StrictModel):
    language: str | None = None
    formality: str | None = None
    greeting: str | None = None
    sign_off: str | None = None
    contacts: list[str] = []
    notes: str = ""


@router.get("/stats", response_model=FeedbackSummary)
async def feedback_stats(
    days: int = Query(default=90, ge=1, le=365),
    _: Principal = Viewer,
    feedback: FeedbackStore = Injected(FeedbackStore),  # type: ignore[type-abstract]
) -> FeedbackSummary:
    rows = await feedback.recent(since=datetime.now(UTC) - timedelta(days=days))
    return FeedbackSummary(
        days=days,
        total=len(rows),
        unchanged=sum(1 for r in rows if r.unchanged),
        edited=sum(1 for r in rows if r.status == "approved" and r.edited),
        rejected=sum(1 for r in rows if r.status == "rejected"),
        expired=sum(1 for r in rows if r.status == "expired"),
        by_kind=group_stats(rows, by_partner=False),
        by_supplier=[s for s in group_stats(rows, by_partner=True) if s.partner_id is not None],
    )


@router.get("/suggestions", response_model=list[Suggestion])
async def list_suggestions(
    _: Principal = Viewer,
    store: SuggestionStore = Injected(SuggestionStore),  # type: ignore[type-abstract]
) -> list[Suggestion]:
    return await store.open()


@router.post("/suggestions/{suggestion_id}/accept", response_model=SuggestionAction)
async def accept_suggestion(
    suggestion_id: int,
    principal: Principal = Admin,
    store: SuggestionStore = Injected(SuggestionStore),  # type: ignore[type-abstract]
    changes: AutonomyChanges = Injected(AutonomyChanges),
    approvals: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
) -> SuggestionAction:
    """Apply what the suggestion proposes: a rule goes through the autonomy change (a
    second person confirms), a setting is saved at once."""
    suggestion = await store.get(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail=f"suggestion {suggestion_id} not found")
    if suggestion.status != "open":
        raise HTTPException(status_code=409, detail=f"suggestion is already {suggestion.status}")
    current = await changes.current_settings()
    approval_id: int | None = None
    saved_version: int | None = None
    if suggestion.kind == "autonomy_rule":
        rule = AutonomyRule.model_validate(suggestion.proposal["rule"])
        rules = [r for r in current.autonomy.rules if r.id != rule.id] + [rule]
        policy = AutonomyPolicy(rules=rules)
        if await _pending_change(approvals) is not None:
            raise HTTPException(
                status_code=409, detail="an autonomy change is already waiting for a second person"
            )
        try:
            approval = await changes.request(
                policy,
                widened=policy.raises_over(current.autonomy),
                requested_by=principal.email,
                requested_by_name=principal.name,
                note=f"suggestion: {suggestion.title}",
            )
        except ScError as exc:
            raise HTTPException(status_code=502, detail=f"Odoo refused: {exc.message}") from exc
        approval_id = approval.id
    elif suggestion.kind == "setting":
        name, value = suggestion.proposal.get("setting"), suggestion.proposal.get("value")
        if name not in type(current).model_fields:
            raise HTTPException(status_code=422, detail=f"unknown setting {name!r}")
        saved = await changes.save_settings(
            current.model_copy(update={str(name): value}),
            changed_by=principal.email,
            note=f"suggestion: {suggestion.title}",
        )
        saved_version = saved.version
    else:
        raise HTTPException(status_code=422, detail="an attention note has nothing to apply")
    closed = await store.close(suggestion_id, status="accepted", by=principal.email)
    logger.bind(suggestion=suggestion.key, by=principal.email).info("suggestion accepted")
    return SuggestionAction(suggestion=closed, saved_version=saved_version, approval_id=approval_id)


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=Suggestion)
async def dismiss_suggestion(
    suggestion_id: int,
    principal: Principal = Approver,
    store: SuggestionStore = Injected(SuggestionStore),  # type: ignore[type-abstract]
) -> Suggestion:
    try:
        return await store.close(suggestion_id, status="dismissed", by=principal.email)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.get("/profiles/{partner_id}", response_model=SupplierProfile)
async def get_profile(
    partner_id: int,
    _: Principal = Viewer,
    profiles: ProfileStore = Injected(ProfileStore),  # type: ignore[type-abstract]
) -> SupplierProfile:
    return await profiles.get(partner_id) or SupplierProfile(partner_id=partner_id)


@router.put("/profiles/{partner_id}", response_model=SupplierProfile)
async def put_profile(
    partner_id: int,
    body: ProfileUpdate,
    principal: Principal = Approver,
    profiles: ProfileStore = Injected(ProfileStore),  # type: ignore[type-abstract]
) -> SupplierProfile:
    current = await profiles.get(partner_id)
    profile = SupplierProfile(
        partner_id=partner_id,
        facts=current.facts if current else {},
        **body.model_dump(),
    )
    saved = await profiles.save(profile, by=principal.email)
    logger.bind(partner_id=partner_id, by=principal.email).info("supplier profile saved")
    return saved
