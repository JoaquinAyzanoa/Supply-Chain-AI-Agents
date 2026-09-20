"""The AI performance page (phase 11 S8): how much runs alone, how fast people decide,
how often they edit or reject, how good the predictions are, and what it costs.

Every measure is computed from records: the automatic-actions feed against
the approvals raised in the period, the decision feedback (turnaround,
edits, rejections), the supplier scorecards (promise drift), the latest
plan's lines (WAPE by class), the vendor bills that matched first time and
the agents' runs (cost). Where the period holds no data the measure is
null, never a guess.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean, median

from fastapi import APIRouter, Query
from fastapi_injector import Injected
from loguru import logger

from director.api.approvals import ApprovalsGateway
from director.api.auth import Principal, Viewer
from director.api.performance import PerformanceSource
from director.api.planning import PlanningReadStore
from director.api.runs import RunsGateway
from director.autonomy import AutoActionsStore
from director.learning import FeedbackStore
from sc_core.odoo.repositories.approval import ApprovalRepo
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError

router = APIRouter(prefix="/ai", tags=["ai"])


class AutomationRate(StrictModel):
    kind: str
    automated: int = 0
    decided: int = 0

    @property
    def rate(self) -> float | None:
        total = self.automated + self.decided
        return round(self.automated / total, 3) if total else None


class AutomationView(StrictModel):
    kind: str
    automated: int
    decided: int
    rate: float | None


class ClassWape(StrictModel):
    abc_class: str
    wape: float | None
    lines: int


class AgentCost(StrictModel):
    agent: str
    runs: int
    cost_usd: float


class AiPerformance(StrictModel):
    days: int
    since: datetime
    automation: list[AutomationView] = []
    automation_rate: float | None = None
    decisions: int = 0
    turnaround_hours_median: float | None = None
    edit_rate: float | None = None
    rejection_rate: float | None = None
    eta_error_days: float | None = None
    wape_by_class: list[ClassWape] = []
    invoices_first_time: int = 0
    invoices_total: int = 0
    invoices_first_time_rate: float | None = None
    negotiation_savings: float | None = None
    runs: int = 0
    cost_usd: float = 0.0
    cases_with_runs: int = 0
    cost_per_case_usd: float | None = None
    by_agent: list[AgentCost] = []


async def build_ai(
    *,
    days: int,
    now: datetime,
    approvals: ApprovalsGateway,
    auto_actions: AutoActionsStore,
    feedback: FeedbackStore,
    performance: PerformanceSource | None,
    planning: PlanningReadStore | None,
    runs: RunsGateway | None,
) -> AiPerformance:
    since = now - timedelta(days=days)
    view = AiPerformance(days=days, since=since)

    # automation rate per kind: what ran alone against what a person had to decide
    rates: dict[str, AutomationRate] = {}
    for action in await auto_actions.recent(since=since, limit=5000):
        rates.setdefault(action.kind, AutomationRate(kind=action.kind))
        rates[action.kind] = rates[action.kind].model_copy(
            update={"automated": rates[action.kind].automated + 1}
        )
    raised = [
        a
        for a in await approvals.list(status="all", kind=None, po_name=None)
        if a.create_date and a.create_date >= since
    ]
    for approval in raised:
        rates.setdefault(approval.kind, AutomationRate(kind=approval.kind))
        rates[approval.kind] = rates[approval.kind].model_copy(
            update={"decided": rates[approval.kind].decided + 1}
        )
    automation = [
        AutomationView(kind=r.kind, automated=r.automated, decided=r.decided, rate=r.rate)
        for r in sorted(rates.values(), key=lambda r: (-(r.automated + r.decided), r.kind))
    ]
    automated_total = sum(r.automated for r in rates.values())
    decided_total = sum(r.decided for r in rates.values())
    view = view.model_copy(
        update={
            "automation": automation,
            "automation_rate": round(automated_total / (automated_total + decided_total), 3)
            if automated_total + decided_total
            else None,
            "decisions": decided_total,
        }
    )

    # how people decided: turnaround, edits, rejections
    rows = await feedback.recent(since=since)
    decided_rows = [r for r in rows if r.status in ("approved", "rejected")]
    seconds = [r.seconds_to_decide for r in decided_rows if r.seconds_to_decide is not None]
    approved = [r for r in decided_rows if r.status == "approved"]
    view = view.model_copy(
        update={
            "turnaround_hours_median": round(median(seconds) / 3600, 1) if seconds else None,
            "edit_rate": round(sum(1 for r in approved if r.edited) / len(approved), 3)
            if approved
            else None,
            "rejection_rate": round(
                sum(1 for r in decided_rows if r.status == "rejected") / len(decided_rows), 3
            )
            if decided_rows
            else None,
        }
    )

    # ETA prediction error: the scorecards' promise drift, in days
    if performance is not None:
        try:
            drifts = [
                abs(float(row["promise_drift_days"]))
                for row in await performance.scores()
                if row.get("promise_drift_days") is not None
            ]
        except ScError as exc:
            logger.warning("scores unavailable for the AI page: {}", exc)
            drifts = []
        if drifts:
            view = view.model_copy(update={"eta_error_days": round(mean(drifts), 1)})

    # forecast error by ABC class, from the latest plan
    if planning is not None:
        latest = await planning.runs(limit=1)
        if latest:
            by_class: dict[str, list[float]] = defaultdict(list)
            counts: dict[str, int] = defaultdict(int)
            for row in await planning.lines(latest[0].run_id):
                counts[row.line.abc_class] += 1
                if row.line.wape is not None:
                    by_class[row.line.abc_class].append(float(row.line.wape))
            view = view.model_copy(
                update={
                    "wape_by_class": [
                        ClassWape(
                            abc_class=cls,
                            wape=round(mean(by_class[cls]), 3) if by_class.get(cls) else None,
                            lines=counts[cls],
                        )
                        for cls in sorted(counts)
                    ]
                }
            )

    # vendor bills matched first time: recorded alone, or raised as clean
    first_time = rates.get("vendor_bill", AutomationRate(kind="vendor_bill")).automated
    total_bills = first_time
    for approval in raised:
        if approval.kind != "vendor_bill":
            continue
        total_bills += 1
        if ApprovalRepo.payload_of(approval).get("verdict") == "clean":
            first_time += 1
    view = view.model_copy(
        update={
            "invoices_first_time": first_time,
            "invoices_total": total_bills,
            "invoices_first_time_rate": round(first_time / total_bills, 3) if total_bills else None,
        }
    )

    # what it cost
    if runs is not None:
        try:
            recent = await runs.recent(since=since, limit=5000)
        except ScError as exc:
            logger.warning("runs unavailable for the AI page: {}", exc)
            recent = []
        cost = sum(r.cost_usd for r in recent)
        cases = {r.case_id for r in recent if r.case_id}
        per_agent: dict[str, list[float]] = defaultdict(list)
        for run in recent:
            per_agent[run.agent].append(run.cost_usd)
        view = view.model_copy(
            update={
                "runs": len(recent),
                "cost_usd": round(cost, 4),
                "cases_with_runs": len(cases),
                "cost_per_case_usd": round(cost / len(cases), 4) if cases else None,
                "by_agent": [
                    AgentCost(agent=agent, runs=len(costs), cost_usd=round(sum(costs), 4))
                    for agent, costs in sorted(per_agent.items(), key=lambda kv: -sum(kv[1]))
                ],
            }
        )
    return view


@router.get("", response_model=AiPerformance)
async def ai_performance(
    days: int = Query(default=30, ge=1, le=365),
    _: Principal = Viewer,
    approvals: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
    auto_actions: AutoActionsStore = Injected(AutoActionsStore),  # type: ignore[type-abstract]
    feedback: FeedbackStore = Injected(FeedbackStore),  # type: ignore[type-abstract]
    performance: PerformanceSource = Injected(PerformanceSource),  # type: ignore[type-abstract]
    planning: PlanningReadStore = Injected(PlanningReadStore),  # type: ignore[type-abstract]
    runs: RunsGateway = Injected(RunsGateway),  # type: ignore[type-abstract]
) -> AiPerformance:
    return await build_ai(
        days=days,
        now=datetime.now(UTC),
        approvals=approvals,
        auto_actions=auto_actions,
        feedback=feedback,
        performance=performance,
        planning=planning,
        runs=runs,
    )
