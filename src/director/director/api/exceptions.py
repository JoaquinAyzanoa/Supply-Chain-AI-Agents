"""The exceptions board: what needs an eye, grouped, with the next automatic step.

Columns: late orders, RFQs without a reply, unlinked emails, failed runs
and stale approvals. Orders carry the follow-up policy's next action and
its date; "act now" runs that step immediately (or chases the supplier
anyway when the policy would wait) in the background, and the case
timeline shows what happened.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi_injector import Injected
from loguru import logger

from director.api.approvals import ApprovalsGateway
from director.api.auth import Approver, Principal, Viewer
from director.policies import FollowUpPolicy, PoFacts, next_action
from director.store import CaseStore
from sc_core.infra.settings import Settings
from sc_core.odoo.links import record_url
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import Conflict, NotFound, ScError
from sc_core.shared.time import local_today

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

ExceptionKind = Literal["late_po", "rfq_no_reply", "unlinked_mail", "failed_run", "stale_approval"]
ACTIONABLE: frozenset[str] = frozenset({"late_po", "rfq_no_reply"})


@runtime_checkable
class ExceptionsSource(Protocol):
    """What the follow-up job knows and can do (``FollowUpJob`` implements it)."""

    async def effective_policy(self) -> FollowUpPolicy: ...

    async def gather(self, today: date) -> list[PoFacts]: ...

    async def act_now(
        self, po_name: str, *, requested_by: str, today: date | None = None
    ) -> dict[str, Any]: ...


class ExceptionItem(StrictModel):
    kind: ExceptionKind
    title: str
    detail: str
    po_id: int | None = None
    po_name: str | None = None
    partner_id: int | None = None
    case_id: str | None = None
    approval_id: int | None = None
    days: int = 0
    next_action: str | None = None
    next_action_at: date | None = None
    can_act: bool = False
    odoo_url: str | None = None


class ExceptionsBoard(StrictModel):
    as_of: date
    late_pos: list[ExceptionItem] = []
    rfqs_no_reply: list[ExceptionItem] = []
    unlinked_mails: list[ExceptionItem] = []
    failed_runs: list[ExceptionItem] = []
    stale_approvals: list[ExceptionItem] = []


class ActResponse(StrictModel):
    accepted: bool
    po_name: str
    message: str


async def build_board(
    *,
    source: ExceptionsSource,
    cases: CaseStore,
    approvals: ApprovalsGateway,
    settings: Settings,
    today: date,
) -> ExceptionsBoard:
    policy = await source.effective_policy()
    facts = await source.gather(today)
    late: list[ExceptionItem] = []
    silent: list[ExceptionItem] = []
    for fact in facts:
        step = next_action(fact, policy, today)
        action = None
        if step is not None:
            action = "escalate to a person" if step.escalate else f"{step.task} ({step.rule})"
        common: dict[str, Any] = {
            "po_id": fact.po_id,
            "po_name": fact.po_name,
            "partner_id": fact.partner_id,
            "next_action": action,
            "next_action_at": step.due if step else None,
            "can_act": not fact.awaiting_human,
            "odoo_url": record_url(settings.odoo.url, "purchase.order", fact.po_id),
        }
        open_cases = await cases.open_for_po(fact.po_name)
        if open_cases:
            common["case_id"] = open_cases[0].case_id
        if fact.is_confirmed_open and fact.date_planned and today > fact.date_planned:
            days = (today - fact.date_planned).days
            late.append(
                ExceptionItem(
                    kind="late_po",
                    title=fact.po_name,
                    detail=f"{days} days past {fact.date_planned.isoformat()} without a receipt",
                    days=days,
                    **common,
                )
            )
        elif (
            fact.is_rfq and (days_silent := fact.silent_days(today)) is not None and days_silent > 0
        ):
            silent.append(
                ExceptionItem(
                    kind="rfq_no_reply",
                    title=fact.po_name,
                    detail=(
                        f"no reply for {days_silent} days ({fact.followups_sent} follow-ups sent)"
                    ),
                    days=days_silent,
                    **common,
                )
            )
    unlinked = [
        ExceptionItem(
            kind="unlinked_mail",
            title=c.summary or "email without an order",
            detail=f"case {c.case_id} ({c.status})",
            case_id=c.case_id,
            days=(today - c.created_at.date()).days,
        )
        for c in await cases.list(kind="unlinked", limit=100)
        if c.is_open
    ]
    failed = [
        ExceptionItem(
            kind="failed_run",
            title=c.po_name or c.case_id,
            detail=c.summary or "run failed",
            po_name=c.po_name,
            case_id=c.case_id,
            days=(today - c.updated_at.date()).days,
        )
        for c in await cases.list(status="failed", limit=50)
    ]
    stale: list[ExceptionItem] = []
    for approval in await approvals.list(status="pending", kind=None, po_name=None):
        if approval.create_date is None:
            continue
        days = (today - approval.create_date.date()).days
        if days < policy.approval_stale_days:
            continue
        case = await cases.find_by_thread(approval.thread_id) if approval.thread_id else None
        stale.append(
            ExceptionItem(
                kind="stale_approval",
                title=approval.summary,
                detail=f"{approval.kind} pending for {days} days",
                po_id=approval.po_id.id if approval.po_id else None,
                po_name=approval.po_id.name if approval.po_id else None,
                case_id=case.case_id if case else None,
                approval_id=approval.id,
                days=days,
                next_action="expires" if approval.kind != "escalation" else "reminder",
                odoo_url=record_url(settings.odoo.url, "sc.approval", approval.id),
            )
        )
    return ExceptionsBoard(
        as_of=today,
        late_pos=sorted(late, key=lambda i: -i.days),
        rfqs_no_reply=sorted(silent, key=lambda i: -i.days),
        unlinked_mails=unlinked,
        failed_runs=failed,
        stale_approvals=sorted(stale, key=lambda i: -i.days),
    )


@router.get("", response_model=ExceptionsBoard)
async def exceptions_board(
    _: Principal = Viewer,
    source: ExceptionsSource = Injected(ExceptionsSource),  # type: ignore[type-abstract]
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    approvals: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
    settings: Settings = Injected(Settings),
) -> ExceptionsBoard:
    return await build_board(
        source=source, cases=cases, approvals=approvals, settings=settings, today=local_today()
    )


@router.post("/{kind}/{po_name}/act", response_model=ActResponse, status_code=202)
async def act_now(
    kind: ExceptionKind,
    po_name: str,
    background: BackgroundTasks,
    principal: Principal = Approver,
    source: ExceptionsSource = Injected(ExceptionsSource),  # type: ignore[type-abstract]
) -> ActResponse:
    """Run the follow-up step for this order now (a model call and an email or an escalation)."""
    if kind not in ACTIONABLE:
        raise HTTPException(status_code=422, detail=f"{kind} has no automatic action")
    today = local_today()
    # Validate synchronously so the caller gets a 404/409, then act in the background:
    # the agent call takes seconds and the case timeline shows the outcome.
    facts = [f for f in await source.gather(today) if f.po_name == po_name]
    if not facts:
        raise HTTPException(status_code=404, detail=f"{po_name} is not an open order")
    if facts[0].awaiting_human:
        raise HTTPException(status_code=409, detail=f"{po_name} already waits for a person")

    async def run() -> None:
        try:
            outcome = await source.act_now(po_name, requested_by=principal.email, today=today)
        except (NotFound, Conflict, ScError) as exc:
            logger.bind(po_name=po_name).warning("act now failed: {}", exc)
            return
        logger.bind(po_name=po_name, by=principal.email, outcome=outcome).info("acted now")

    background.add_task(run)
    return ActResponse(accepted=True, po_name=po_name, message="follow-up started")
