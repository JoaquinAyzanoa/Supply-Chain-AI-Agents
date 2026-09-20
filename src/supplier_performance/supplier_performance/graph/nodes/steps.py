"""The weekly run, node by node.

load_history gathers the period's history per supplier; compute turns it
into scores (code only) and saves the observations and the scores as a run
that is not yet applied; scorecards asks the model for one paragraph per
supplier; the ``supplier_score`` approval carries every score; apply writes
the partner fields, the price list lead times and the planner's parameters.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from sc_core.graph import ApprovalRequest, clear_sensitive, decision_for
from sc_core.i18n import Language, t
from sc_core.infra import tracing
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import Outcome, OutcomeStatus, SupplierPerformanceTask, SupplierScore
from supplier_performance.domain.metrics import Weights, score_supplier
from supplier_performance.domain.models import SupplierHistory
from supplier_performance.graph.state import Node
from supplier_performance.infra.ports import PerformancePorts

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
SCORE_STEP = "supplier_score"


def task_of(state: dict[str, Any]) -> SupplierPerformanceTask:
    return SupplierPerformanceTask.model_validate(state["task"])


def finish(status: OutcomeStatus, summary: str, **extra: Any) -> dict[str, Any]:
    return {
        "outcome": Outcome(status=status, summary=summary[:500]).model_dump(mode="json"),
        **clear_sensitive(),
        **extra,
    }


def make_load_history(
    ports: PerformancePorts, *, months: int, max_suppliers: int, today: Callable[[], date]
) -> Node:
    async def load_history(state: Any) -> dict[str, Any]:
        task = task_of(state)
        end = task.as_of or today()
        start = end - timedelta(days=30 * months)
        if state.get("run_id"):
            await ports.start_run(
                run_id=state["run_id"],
                case_id=state["case_id"],
                model=state.get("model"),
                trace_url=tracing.trace_url(state.get("trace_id")),
            )
        suppliers = await ports.suppliers_with_activity(start)
        if task.partner_ids:
            wanted = set(task.partner_ids)
            suppliers = [s for s in suppliers if s[0] in wanted]
        suppliers = suppliers[:max_suppliers]
        if not suppliers:
            return {
                "period": {"start": start.isoformat(), "end": end.isoformat()},
                **finish("no_action", t("score.nobody", "en", since=start.isoformat())),
            }
        histories = [
            (await ports.history(pid, name, start=start, end=end)).model_dump(mode="json")
            for pid, name in suppliers
        ]
        previous = {
            str(k): v.model_dump(mode="json") for k, v in (await ports.previous_scores()).items()
        }
        logger.bind(suppliers=len(histories), start=str(start), end=str(end)).info("history loaded")
        return {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "histories": histories,
            "previous": previous,
        }

    return load_history


def make_compute(ports: PerformancePorts, *, weights: Weights) -> Node:
    async def compute(state: Any) -> dict[str, Any]:
        histories = [SupplierHistory.model_validate(h) for h in state.get("histories") or []]
        previous = {
            int(k): SupplierScore.model_validate(v)
            for k, v in (state.get("previous") or {}).items()
        }
        scores = [
            score_supplier(h, weights=weights, previous=previous.get(h.partner_id))
            for h in histories
        ]
        await ports.save_observations(histories)
        await ports.save_run(state.get("run_id") or "run_unknown", scores)
        logger.bind(scored=len(scores)).info("scores computed")
        return {"scores": [s.model_dump(mode="json") for s in scores]}

    return compute


class ScorecardText(BaseModel):
    """What the model returns for one supplier."""

    scorecard: str = Field(min_length=20, max_length=900)
    trends: list[str] = Field(default_factory=list, max_length=5)


def make_scorecards(
    chat: ChatCompleter,
    ports: PerformancePorts,
    *,
    langfuse: LangfuseCfg | None,
    language: Language = "en",
) -> Node:
    async def scorecards(state: Any) -> dict[str, Any]:
        prompt = get_prompt("scorecard", local_dir=PROMPTS_DIR, cfg=langfuse)
        written: list[dict[str, Any]] = []
        for raw in state.get("scores") or []:
            score = SupplierScore.model_validate(raw)
            facts = _facts(score)
            text = await complete_structured(
                chat,
                [system(prompt.compile(language=language)), user(facts)],
                ScorecardText,
                name="supplier_performance.scorecard",
                metadata={
                    "prompt": prompt.name,
                    "prompt_version": prompt.version,
                    "partner_id": score.partner_id,
                },
            )
            trends = list(dict.fromkeys([*score.trends, *text.trends]))[:5]
            written.append(
                score.model_copy(update={"scorecard": text.scorecard, "trends": trends}).model_dump(
                    mode="json"
                )
            )
        # the run row carries the text from now on, so the Suppliers page shows it while
        # the approval waits
        await ports.save_run(
            state.get("run_id") or "run_unknown", [SupplierScore.model_validate(w) for w in written]
        )
        return {"scores": written}

    return scorecards


def _facts(score: SupplierScore) -> str:
    def pct(value: float | None) -> str:
        return f"{100 * value:.0f}%" if value is not None else "no data"

    def num(value: float | None, unit: str) -> str:
        return f"{value:g} {unit}" if value is not None else "no data"

    lines = [
        f"Supplier: {score.partner_name}",
        f"Period: {score.period_start.isoformat()} to {score.period_end.isoformat()}",
        f"Score: {score.score:g} / 100",
        f"OTIF (on time and in full): {pct(score.otif)} over {score.samples.get('lines', 0)} lines",
        f"Observed lead time: {num(score.lead_time_mean_days, 'days')} "
        f"(std {num(score.lead_time_sigma_days, 'days')})",
        f"Promise drift: {num(score.promise_drift_days, 'days')} over "
        f"{score.samples.get('orders', 0)} orders (positive = later than promised)",
        f"Reply time: {num(score.response_hours_median, 'hours median')} over "
        f"{score.samples.get('replies', 0)} replies",
        f"Receipt problems: {pct(score.quality_rate)} of lines",
        f"Price stability: coefficient of variation {num(score.price_cv, '')} over "
        f"{score.samples.get('priced_products', 0)} products",
    ]
    if score.trends:
        lines.append("Changes since the previous run: " + "; ".join(score.trends))
    return "\n".join(lines)


def make_score_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        scores = [SupplierScore.model_validate(s) for s in state.get("scores") or []]
        period = state.get("period") or {}
        flagged = sum(1 for s in scores if s.trends)
        return ApprovalRequest(
            kind="supplier_score",
            summary=t(
                "score.approval_summary",
                language,
                n=len(scores),
                end=period.get("end", "-"),
                flagged=flagged,
            )[:200],
            payload={
                "run_id": state.get("run_id"),
                "period": period,
                "scores": [s.model_dump(mode="json") for s in scores],
                "writes": "partner score fields, price list lead times, planner lead times",
            },
            res_model="sc.approval",
            res_id=None,
            review_on_approval=True,
        )

    return build


def make_apply(ports: PerformancePorts, *, language: Language = "en") -> Node:
    async def apply(state: Any) -> dict[str, Any]:
        scores = [SupplierScore.model_validate(s) for s in state.get("scores") or []]
        decision = decision_for(state, SCORE_STEP)
        approval_id = decision.approval_id if decision and decision.approval_id else None
        run_id = state.get("run_id") or "run_unknown"
        await ports.save_run(run_id, scores)  # with the scorecards
        applied = await ports.apply(run_id, scores, approval_id=approval_id)
        logger.bind(run_id=run_id, **applied).info("scores applied")
        return finish(
            "applied",
            t(
                "score.applied_summary",
                language,
                partners=applied.get("partners", 0),
                entries=applied.get("price_list_entries", 0),
                params=applied.get("planning_params", 0),
            ),
            applied=applied,
        )

    return apply


def make_rejected(*, language: Language = "en") -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        decision = decision_for(state, SCORE_STEP)
        who = decision.resolved_by if decision and decision.resolved_by else "-"
        reason = (
            decision.reason if decision and decision.reason else t("common.no_reason", language)
        )
        return finish("rejected", t("score.rejected_summary", language, who=who, reason=reason))

    return rejected
