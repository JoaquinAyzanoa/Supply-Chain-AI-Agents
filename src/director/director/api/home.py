"""The Home page's numbers (phase 11 S8): KPIs with a trend and what needs you.

Every figure comes from records the director already reads: the supplier
scorecards (service level), the follow-up job's order facts (late and
silent orders), the pending approvals (count and age), Odoo's confirmed
orders (spend this month against last month), the agents' runs (AI cost)
and the automatic-actions feed. The briefing and the feed themselves come
from their own endpoints; this one is the dashboard's header.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import median

from fastapi import APIRouter
from fastapi_injector import Injected
from loguru import logger

from director.api.approvals import ApprovalsGateway
from director.api.auth import Principal, Viewer
from director.api.board import BoardOrders
from director.api.exceptions import ExceptionsSource
from director.api.performance import PerformanceSource
from director.api.runs import RunsGateway
from director.autonomy import AutoActionsStore
from director.briefing import BriefingItem, needs_you_items
from sc_core.i18n import t
from sc_core.infra.settings import Settings
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.time import local_today, utc_now

router = APIRouter(prefix="/home", tags=["home"])


class Kpi(StrictModel):
    key: str
    value: float | None = None
    previous: float | None = None
    unit: str = "count"  # count | pct | days | money | usd
    currency: str | None = None
    detail: str | None = None


class HomeView(StrictModel):
    as_of: date
    kpis: list[Kpi] = []
    needs_you: list[BriefingItem] = []
    pending: int = 0
    late: int = 0
    silent: int = 0
    automated_week: int = 0


def month_start(day: date) -> date:
    return day.replace(day=1)


def previous_month_start(day: date) -> date:
    first = month_start(day)
    return (first - timedelta(days=1)).replace(day=1)


async def build_home(
    *,
    approvals: ApprovalsGateway,
    exceptions: ExceptionsSource,
    performance: PerformanceSource | None,
    orders: BoardOrders | None,
    runs: RunsGateway | None,
    auto_actions: AutoActionsStore | None,
    today: date,
    now: datetime,
    language: str = "en",
) -> HomeView:
    kpis: list[Kpi] = []

    # service level: the mean OTIF of the scored suppliers
    otif: list[float] = []
    if performance is not None:
        try:
            for row in await performance.scores():
                if row.get("otif") is not None:
                    otif.append(float(row["otif"]))
        except ScError as exc:
            logger.warning("scores unavailable for the home page: {}", exc)
    kpis.append(
        Kpi(
            key="service_level",
            value=round(100 * sum(otif) / len(otif), 1) if otif else None,
            unit="pct",
            detail=(
                t("home.scored", language, n=len(otif))
                if otif
                else t("home.no_scorecard", language)
            ),
        )
    )

    # late and silent orders, as the follow-up job sees them
    facts = await exceptions.gather(today)
    late = sum(
        1
        for f in facts
        if f.is_confirmed_open and f.date_planned is not None and f.date_planned < today
    )
    silent = sum(1 for f in facts if f.is_rfq and (f.silent_days(today) or 0) > 0)
    kpis.append(
        Kpi(
            key="late_orders",
            value=late,
            unit="count",
            detail=t("home.silent", language, n=silent),
        )
    )

    # pending approvals and how long they have waited
    pending = await approvals.list(status="pending", kind=None, po_name=None)
    ages = [(now - a.create_date).total_seconds() / 86400 for a in pending if a.create_date]
    kpis.append(
        Kpi(
            key="pending_approvals",
            value=len(pending),
            previous=round(median(ages), 1) if ages else None,
            unit="count",
            detail=t("home.median_age", language, days=median(ages)) if ages else None,
        )
    )

    # spend this month against last month (confirmed orders by their approval date)
    this_month, last_month, currency = 0.0, 0.0, None
    if orders is not None:
        try:
            for po in await orders.board_orders(closed_since=previous_month_start(today)):
                if po.state not in ("purchase", "done"):
                    continue
                when = (po.date_approve or po.date_order or datetime.min).date()
                if when >= month_start(today):
                    this_month += po.amount_total
                elif when >= previous_month_start(today):
                    last_month += po.amount_total
                currency = currency or (po.currency_id.name if po.currency_id else None)
        except ScError as exc:
            logger.warning("orders unavailable for the home page: {}", exc)
    kpis.append(
        Kpi(
            key="spend_month",
            value=round(this_month, 2),
            previous=round(last_month, 2),
            unit="money",
            currency=currency,
        )
    )

    # what the agents cost this month, against last month
    cost_now, cost_prev = 0.0, 0.0
    if runs is not None:
        try:
            since = datetime.combine(
                previous_month_start(today), datetime.min.time(), tzinfo=now.tzinfo
            )
            for run in await runs.recent(since=since, limit=5000):
                started = run.started_at.date() if run.started_at else today
                if started >= month_start(today):
                    cost_now += run.cost_usd
                else:
                    cost_prev += run.cost_usd
        except ScError as exc:
            logger.warning("runs unavailable for the home page: {}", exc)
    kpis.append(
        Kpi(key="ai_cost_month", value=round(cost_now, 2), previous=round(cost_prev, 2), unit="usd")
    )

    automated = 0
    if auto_actions is not None:
        automated = len(await auto_actions.recent(since=now - timedelta(days=7), limit=500))
    kpis.append(Kpi(key="automated_week", value=automated, unit="count"))

    return HomeView(
        as_of=today,
        kpis=kpis,
        needs_you=await needs_you_items(approvals, now=now, language=language, limit=8),  # type: ignore[arg-type]
        pending=len(pending),
        late=late,
        silent=silent,
        automated_week=automated,
    )


@router.get("", response_model=HomeView)
async def home(
    _: Principal = Viewer,
    approvals: ApprovalsGateway = Injected(ApprovalsGateway),  # type: ignore[type-abstract]
    exceptions: ExceptionsSource = Injected(ExceptionsSource),  # type: ignore[type-abstract]
    performance: PerformanceSource = Injected(PerformanceSource),  # type: ignore[type-abstract]
    orders: BoardOrders = Injected(BoardOrders),  # type: ignore[type-abstract]
    runs: RunsGateway = Injected(RunsGateway),  # type: ignore[type-abstract]
    auto_actions: AutoActionsStore = Injected(AutoActionsStore),  # type: ignore[type-abstract]
    settings: Settings = Injected(Settings),
) -> HomeView:
    return await build_home(
        approvals=approvals,
        exceptions=exceptions,
        performance=performance,
        orders=orders,
        runs=runs,
        auto_actions=auto_actions,
        today=local_today(),
        now=utc_now(),
        language=settings.agents.language,
    )


__all__ = ["HomeView", "Kpi", "build_home", "router"]
