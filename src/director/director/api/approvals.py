"""The approvals inbox and the one way to resolve an approval from outside Odoo.

``GET /api/approvals`` lists ``sc.approval`` rows with their payload (the
email preview, the proposed changes, the plan totals) and the context the
inbox card needs: why the agent proposed it (the follow-up rule or the
model's summary from the case), and links to Odoo, the Outlook draft and
the Langfuse trace.

``POST /api/approvals/{id}/resolve`` validates the approver's edits for
the approval's kind, then resolves the ``sc.approval`` in Odoo with the
person's name and the details; Odoo fires the agent callback exactly as it
does for its own buttons, so both entry points behave identically. A
second resolve is a 409.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field, ValidationError, model_validator

from director.api.auth import Approver, Principal, Viewer
from director.autonomy import AutonomyChanges
from director.learning import FeedbackRecorder
from director.playbooks import PlaybookEngine, PlaybookPosition
from director.store import Case, CaseStore
from sc_core.infra import tracing
from sc_core.infra.settings import Settings
from sc_core.odoo.links import record_url
from sc_core.odoo.models import Approval, ApprovalStatus
from sc_core.odoo.repositories import ApprovalRepo
from sc_core.schema.autonomy import Reasoning
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import NotFound, ScError

router = APIRouter(prefix="/approvals", tags=["approvals"])

ListStatus = Literal["pending", "approved", "rejected", "expired", "all"]


# --- what the inbox shows ---------------------------------------------------------------------


class PlaybookLookup(Protocol):
    async def position_for_case(self, case_id: str) -> PlaybookPosition | None: ...


class ApprovalLinks(StrictModel):
    odoo: str | None = None
    order: str | None = None
    outlook: str | None = None
    trace: str | None = None


class ApprovalView(StrictModel):
    id: int
    kind: str
    status: str
    summary: str
    po_id: int | None = None
    po_name: str | None = None
    requested_by: str | None = None
    case_id: str | None = None
    case_code: str | None = None
    thread_id: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    reason: str | None = None
    payload: dict[str, Any] = {}
    why: str | None = Field(default=None, description="rule or model reasoning behind it")
    reasoning: Reasoning | None = Field(
        default=None, description="facts, rule or step, confidence, alternatives, counterfactual"
    )
    links: ApprovalLinks = ApprovalLinks()
    playbook: PlaybookPosition | None = None  # the plan this approval sits in, and what follows


# --- the decision ----------------------------------------------------------------------------


class EmailEdits(StrictModel):
    subject: str | None = Field(default=None, max_length=200)
    html_body: str | None = Field(default=None, max_length=100_000)


class ChangeEdits(StrictModel):
    accepted_line_ids: list[int] = Field(description="purchase order line ids to apply")


class PlanEdits(StrictModel):
    accepted_line_ids: list[str]
    edits: dict[str, dict[str, float]] = {}
    params: dict[
        str, dict[str, float]
    ] = {}  # per line: service_level, review_period_days, max_coverage_days


class AwardEdits(StrictModel):
    partner_id: int | None = Field(default=None, description="one supplier for every line")
    lines: dict[str, int] = Field(
        default_factory=dict, description="product id -> supplier id, when the award is split"
    )

    @model_validator(mode="after")
    def _one_or_the_other(self) -> AwardEdits:
        if self.partner_id is None and not self.lines:
            raise ValueError("choose a supplier, or one per line")
        return self


class OfferEdits(StrictModel):
    offered_price: float = Field(gt=0, description="the unit price we ask; within the cap")


class PartnerEdits(StrictModel):
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200)


class RequestEdits(StrictModel):
    accepted_items: list[int] = Field(description="indexes of the items to order")
    partner_id: int | None = Field(default=None, description="one supplier for every item")


class PriceListEdits(StrictModel):
    accepted_codes: list[str] = Field(description="the rows to write, by their code")


EDITS_BY_KIND: dict[str, type[StrictModel]] = {
    "award": AwardEdits,
    "negotiation_offer": OfferEdits,
    "partner_create": PartnerEdits,
    "internal_request": RequestEdits,
    "price_list_update": PriceListEdits,
    "send_email": EmailEdits,
    "po_change": ChangeEdits,
    "planning_run": PlanEdits,
}


class ResolveRequest(StrictModel):
    status: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=1000)
    edited_payload: dict[str, Any] | None = Field(
        default=None, description="per kind: EmailEdits, ChangeEdits or PlanEdits"
    )


class ResolveResponse(StrictModel):
    id: int
    status: str
    resolved_by: str
    callback_status: str | None = None


class BulkRequest(StrictModel):
    ids: list[int] = Field(min_length=1, max_length=100)
    status: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=1000)


class BulkResult(StrictModel):
    id: int
    status: str | None = None
    error: str | None = None


class BulkResponse(StrictModel):
    resolved: int
    results: list[BulkResult]


# --- gateway to Odoo ---------------------------------------------------------------------------


@runtime_checkable
class ApprovalsGateway(Protocol):
    async def list(
        self, *, status: ListStatus, kind: str | None, po_name: str | None
    ) -> list[Approval]: ...

    async def get(self, approval_id: int) -> Approval: ...

    async def create(
        self,
        *,
        kind: str,
        summary: str,
        payload: dict[str, Any],
        requested_by: str,
        case_id: str,
        po_id: int | None = None,
    ) -> Approval: ...

    async def resolve(
        self,
        approval_id: int,
        status: ApprovalStatus,
        *,
        by_name: str,
        reason: str | None,
        details: dict[str, Any] | None,
    ) -> Approval: ...


class OdooApprovalsGateway:
    def __init__(self, approvals: ApprovalRepo) -> None:
        self._approvals = approvals

    async def list(
        self, *, status: ListStatus, kind: str | None, po_name: str | None
    ) -> list[Approval]:
        domain: list[Any] = []
        if status != "all":
            domain.append(["status", "=", status])
        if kind:
            domain.append(["kind", "=", kind])
        if po_name:
            domain.append(["po_id.name", "=", po_name])
        return await self._approvals.find(domain, order="create_date desc, id desc", limit=200)

    async def get(self, approval_id: int) -> Approval:
        return await self._approvals.get(approval_id)

    async def create(
        self,
        *,
        kind: str,
        summary: str,
        payload: dict[str, Any],
        requested_by: str,
        case_id: str,
        po_id: int | None = None,
    ) -> Approval:
        return await self._approvals.create(
            kind=kind,  # type: ignore[arg-type]
            summary=summary,
            payload=payload,
            requested_by=requested_by,
            case_id=case_id,
            po_id=po_id,
        )

    async def resolve(
        self,
        approval_id: int,
        status: ApprovalStatus,
        *,
        by_name: str,
        reason: str | None,
        details: dict[str, Any] | None,
    ) -> Approval:
        return await self._approvals.resolve_via_api(
            approval_id, status, by_name=by_name, reason=reason, details=details
        )


# --- helpers -----------------------------------------------------------------------------------


async def build_view(
    approval: Approval,
    *,
    settings: Settings,
    cases: CaseStore,
    playbooks: PlaybookLookup | None = None,
) -> ApprovalView:
    payload = ApprovalRepo.payload_of(approval)
    case = await case_for(cases, approval)
    position = await playbooks.position_for_case(case.case_id) if playbooks and case else None
    why = await _why(cases, case.case_id) if case else None
    reasoning = reasoning_of(payload, position, why)
    trace_id = case.trace_id if case else None
    links = ApprovalLinks(
        odoo=record_url(settings.odoo.browser_url, "sc.approval", approval.id),
        order=record_url(settings.odoo.browser_url, "purchase.order", approval.po_id.id)
        if approval.po_id
        else None,
        outlook=payload.get("web_link"),
        trace=tracing.trace_url(trace_id),
    )
    return ApprovalView(
        id=approval.id,
        kind=approval.kind,
        status=approval.status,
        summary=approval.summary,
        po_id=approval.po_id.id if approval.po_id else None,
        po_name=approval.po_id.name if approval.po_id else None,
        requested_by=approval.requested_by,
        case_id=case.case_id if case else approval.case_id,
        case_code=case.code if case else None,
        thread_id=approval.thread_id,
        created_at=approval.create_date,
        resolved_at=approval.resolved_at,
        resolved_by=approval.resolved_by_name
        or (approval.resolved_by_id.name if approval.resolved_by_id else None),
        reason=approval.reason,
        playbook=position,
        payload=payload,
        why=why,
        reasoning=reasoning,
        links=links,
    )


def reasoning_of(
    payload: dict[str, Any], position: PlaybookPosition | None, why: str | None
) -> Reasoning | None:
    """The reasoning the agent stored, completed with the playbook step and the case's
    last rule when the request itself did not name one."""
    raw = payload.get("reasoning")
    try:
        reasoning = Reasoning.model_validate(raw) if isinstance(raw, dict) else None
    except ValidationError:
        reasoning = None
    if position is not None:
        step = f"playbook {position.title}, step {position.step_label or position.step_id}"
        if reasoning is None:
            reasoning = Reasoning(rule=step)
        elif not reasoning.rule or reasoning.rule.startswith("no autonomy"):
            reasoning = reasoning.model_copy(update={"rule": step})
        else:
            reasoning = reasoning.model_copy(update={"rule": f"{reasoning.rule} · {step}"})
    if reasoning is None and why:
        reasoning = Reasoning(rule=why)
    return reasoning


async def case_for(cases: CaseStore, approval: Approval) -> Case | None:
    """The case behind an approval: the thread a task was sent on, or the case itself.

    Agents' approvals carry the task's thread id; the director's escalations
    carry the case id directly.
    """
    thread_id = approval.thread_id or approval.case_id
    if not thread_id:
        return None
    return await cases.find_by_thread(thread_id) or await cases.get(thread_id)


async def _why(cases: CaseStore, case_id: str) -> str | None:
    """The last rule that fired, else the last agent summary, on the case."""
    events = await cases.events(case_id)
    for event in reversed(events):
        if event.kind == "rule_fired" and event.payload.get("reason"):
            return f"{event.payload.get('rule')}: {event.payload['reason']}"
    for event in reversed(events):
        if event.kind == "result" and event.payload.get("summary"):
            return str(event.payload["summary"])
    for event in reversed(events):
        if event.kind == "escalated" and event.payload.get("reason"):
            return str(event.payload["reason"])
    return None


def validate_edits(kind: str, edited: dict[str, Any] | None) -> dict[str, Any] | None:
    if edited is None:
        return None
    schema = EDITS_BY_KIND.get(kind)
    if schema is None:
        raise HTTPException(status_code=422, detail=f"{kind} approvals take no edits")
    try:
        data = schema.model_validate(edited).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if data.get("params") == {}:
        data.pop("params")  # only planning reviews that keep parameters carry them
    return data


# --- routes --------------------------------------------------------------------------------------


@router.get("", response_model=list[ApprovalView])
async def list_approvals(
    status: ListStatus = Query(default="pending"),
    kind: str | None = Query(default=None),
    po: str | None = Query(default=None),
    _: Principal = Viewer,
    gateway: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    settings: Settings = Injected(Settings),
) -> list[ApprovalView]:
    rows = await gateway.list(status=status, kind=kind, po_name=po)
    return [await build_view(row, settings=settings, cases=cases) for row in rows]


@router.get("/{approval_id}", response_model=ApprovalView)
async def get_approval(
    approval_id: int,
    _: Principal = Viewer,
    gateway: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    settings: Settings = Injected(Settings),
    playbooks: PlaybookEngine = Injected(PlaybookEngine),
) -> ApprovalView:
    try:
        approval = await gateway.get(approval_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found") from exc
    return await build_view(approval, settings=settings, cases=cases, playbooks=playbooks)


@router.post("/bulk", response_model=BulkResponse)
async def resolve_many(
    body: BulkRequest,
    principal: Principal = Approver,
    gateway: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    autonomy: AutonomyChanges = Injected(AutonomyChanges),
    feedback: FeedbackRecorder = Injected(FeedbackRecorder),
) -> BulkResponse:
    """Decide many at once (S8): each one goes through the same path as a single
    decision; the ones that cannot be decided are reported, not skipped silently."""
    results: list[BulkResult] = []
    for approval_id in dict.fromkeys(body.ids):
        try:
            done = await resolve_approval(
                approval_id,
                ResolveRequest(status=body.status, reason=body.reason, edited_payload=None),
                principal=principal,
                gateway=gateway,
                cases=cases,
                autonomy=autonomy,
                feedback=feedback,
            )
        except HTTPException as exc:
            detail = str(exc.detail)
            error = (
                "not found"
                if exc.status_code == 404
                else detail.replace(f"approval {approval_id} is ", "")
                if exc.status_code == 409
                else detail
            )
            results.append(BulkResult(id=approval_id, error=error))
            continue
        results.append(BulkResult(id=approval_id, status=done.status))
    resolved = sum(1 for r in results if r.error is None)
    logger.bind(by=principal.email, resolved=resolved, asked=len(body.ids)).info(
        "approvals decided in bulk"
    )
    return BulkResponse(resolved=resolved, results=results)


@router.post("/{approval_id}/resolve", response_model=ResolveResponse)
async def resolve_approval(
    approval_id: int,
    body: ResolveRequest,
    principal: Principal = Approver,
    gateway: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    autonomy: AutonomyChanges = Injected(AutonomyChanges),
    feedback: FeedbackRecorder = Injected(FeedbackRecorder),
) -> ResolveResponse:
    try:
        approval = await gateway.get(approval_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found") from exc
    if approval.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"approval {approval_id} is already {approval.status}"
        )
    details = (
        validate_edits(approval.kind, body.edited_payload) if body.status == "approved" else None
    )
    if approval.kind == "autonomy_change" and body.status == "approved":
        # widening autonomy is a two-person decision: the requester cannot confirm it
        if AutonomyChanges.second_person_required(approval, principal.email):
            raise HTTPException(
                status_code=403, detail="a second person must approve an autonomy change"
            )
    try:
        resolved = await gateway.resolve(
            approval_id, body.status, by_name=principal.name, reason=body.reason, details=details
        )
    except ScError as exc:
        raise HTTPException(status_code=502, detail=f"Odoo refused: {exc.message}") from exc
    await feedback.record_resolution(
        approval,
        status=body.status,
        by=principal.email,
        via="api",
        reason=body.reason,
        details=details,
    )
    if approval.kind == "autonomy_change" and body.status == "approved":
        await autonomy.apply(approval, by=principal.email)
    case = await case_for(cases, approval)
    if case is not None:
        await cases.add_event(
            case.case_id,
            "approval_resolved",
            {
                "approval_id": approval_id,
                "kind": approval.kind,
                "status": body.status,
                "by": principal.email,
                "via": "api",
                "details": json.loads(json.dumps(details, default=str)) if details else None,
            },
        )
    logger.bind(approval_id=approval_id, status=body.status, by=principal.email).info(
        "approval resolved from the Control Tower"
    )
    return ResolveResponse(
        id=approval_id,
        status=resolved.status,
        resolved_by=principal.name,
        callback_status=resolved.callback_status,
    )
