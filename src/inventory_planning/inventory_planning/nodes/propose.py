"""Graph nodes around the pure steps: load, forecast, compute, detect, explain, propose.

Each node reads typed models out of the state dict and writes plain dicts
back. ``propose`` stores the proposal (numbers only) in ``planning_runs``
and ``planning_lines`` before the approval is asked for.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from loguru import logger

from inventory_planning.forecasting import ForecastResult
from inventory_planning.models import PlanningDataset
from inventory_planning.nodes.compute_policy import compute_lines
from inventory_planning.nodes.detect_exceptions import detect_all
from inventory_planning.nodes.explain import explain_lines, explain_run
from inventory_planning.nodes.forecast import forecast_all
from inventory_planning.nodes.load_data import load_dataset
from inventory_planning.policy import ParamsStore, ProductParams, abc_classes
from inventory_planning.policy.params import resolve_params
from inventory_planning.ports import DataPorts
from inventory_planning.runs import RunStore
from inventory_planning.state import Node
from sc_core.i18n import Language, t
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import LangfuseCfg, PlanningCfg
from sc_core.llm import ChatCompleter
from sc_core.schema.a2a import InventoryPlanningTask
from sc_core.schema.planning import ReplenishmentLine, ReplenishmentProposal
from sc_core.schema.runtime_settings import RuntimeSettings


def task_of(state: dict[str, Any]) -> InventoryPlanningTask:
    return InventoryPlanningTask.model_validate(state["task"])


def dataset_of(state: dict[str, Any]) -> PlanningDataset:
    return PlanningDataset.model_validate(state["dataset"])


def lines_of(state: dict[str, Any]) -> list[ReplenishmentLine]:
    return [ReplenishmentLine.model_validate(row) for row in state.get("lines") or []]


def params_of(state: dict[str, Any]) -> dict[int, ProductParams]:
    return {int(k): ProductParams.model_validate(v) for k, v in (state.get("params") or {}).items()}


def fail(message: str) -> dict[str, Any]:
    return {"outcome": {"status": "failed", "summary": message[:500]}}


def make_load(ports: DataPorts, cfg: PlanningCfg, *, today: Callable[[], date]) -> Node:
    async def load(state: Any) -> dict[str, Any]:
        task = task_of(state)
        as_of = task.as_of or today()
        dataset = await load_dataset(
            ports,
            as_of=as_of,
            history_days=cfg.history_days,
            category=cfg.product_category or None,
            warehouse_code=task.warehouse_code or cfg.warehouse_code or None,
            product_ids=task.product_ids or None,
        )
        if not dataset.products:
            return fail("no plannable products found")
        return {"dataset": dataset.model_dump(mode="json")}

    return load


def make_forecast() -> Node:
    async def forecast(state: Any) -> dict[str, Any]:
        dataset = dataset_of(state)
        results = forecast_all(dataset)
        return {"forecasts": {str(pid): _forecast_dict(r) for pid, r in results.items()}}

    return forecast


def make_compute(
    params_store: ParamsStore, cfg: PlanningCfg, *, runtime: RuntimeSettingsReader | None = None
) -> Node:
    async def compute(state: Any) -> dict[str, Any]:
        task = task_of(state)
        dataset = dataset_of(state)
        forecasts = {int(k): _forecast_from(v) for k, v in state["forecasts"].items()}
        ids = [p.product_id for p in dataset.products]
        revenue = {
            p.product_id: p.list_price * p.demand_between(dataset.history_start, dataset.as_of)
            for p in dataset.products
        }
        stored = await params_store.for_products(ids)
        params = resolve_params(ids, stored, abc_classes(revenue))
        if runtime is not None:
            defaults = await runtime.current()
            params = {pid: _runtime_defaults(p, defaults) for pid, p in params.items()}
        params = {pid: _override(p, task) for pid, p in params.items()}
        lines = compute_lines(
            dataset,
            forecasts,
            params,
            run_id=state["run_id"],
            lead_time_sigma_ratio=cfg.lead_time_sigma_ratio,
        )
        return {
            "params": {str(pid): p.model_dump(mode="json") for pid, p in params.items()},
            "lines": [ln.model_dump(mode="json") for ln in lines],
        }

    return compute


def _runtime_defaults(params: ProductParams, defaults: RuntimeSettings) -> ProductParams:
    """Control Tower planning defaults replace the ABC class defaults, never tuned params."""
    if params.source != "default":
        return params
    changes: dict[str, Any] = {}
    if defaults.planning_service_level is not None:
        changes["service_level"] = defaults.planning_service_level
    if defaults.planning_review_period_days is not None:
        changes["review_period_days"] = defaults.planning_review_period_days
    if defaults.planning_max_coverage_days is not None:
        changes["max_coverage_days"] = defaults.planning_max_coverage_days
    return params.model_copy(update=changes) if changes else params


def make_detect() -> Node:
    async def detect(state: Any) -> dict[str, Any]:
        dataset = dataset_of(state)
        promised = {
            p.product_id: float(p.preferred_supplier.delay_days)
            for p in dataset.products
            if p.preferred_supplier
        }
        lines = detect_all(lines_of(state), params_of(state), promised)
        return {"lines": [ln.model_dump(mode="json") for ln in lines]}

    return detect


def make_explain(
    chat: ChatCompleter, *, langfuse: LangfuseCfg | None, language: Language = "en"
) -> Node:
    async def explain(state: Any) -> dict[str, Any]:
        dataset = dataset_of(state)
        lines = await explain_lines(
            chat, lines_of(state), as_of=dataset.as_of, langfuse=langfuse, language=language
        )
        return {"lines": [ln.model_dump(mode="json") for ln in lines]}

    return explain


def make_propose(
    chat: ChatCompleter,
    runs: RunStore,
    *,
    langfuse: LangfuseCfg | None,
    language: Language = "en",
) -> Node:
    async def propose(state: Any) -> dict[str, Any]:
        task = task_of(state)
        dataset = dataset_of(state)
        lines = lines_of(state)
        draft = ReplenishmentProposal(
            run_id=state["run_id"],
            as_of=dataset.as_of,
            warehouse_id=dataset.warehouse_id,
            warehouse_code=dataset.warehouse_code,
            lines=lines,
        )
        totals = draft.compute_totals()
        summary = await explain_run(
            chat,
            lines,
            totals,
            as_of=dataset.as_of,
            warehouse_code=dataset.warehouse_code,
            langfuse=langfuse,
            language=language,
        )
        proposal = draft.model_copy(update={"totals": totals, "summary": summary})
        status = "simulated" if task.kind == "what_if" else "proposed"
        await runs.save_proposal(proposal, case_id=state["case_id"], kind=task.kind, status=status)
        logger.bind(run_id=state["run_id"], lines=len(lines), totals=totals).info("proposal ready")
        update: dict[str, Any] = {"proposal": proposal.model_dump(mode="json")}
        if task.kind == "what_if":
            update["outcome"] = {
                "status": "no_action",
                "summary": t("plan.simulation", language, summary=summary)[:500],
            }
        return update

    return propose


def _override(params: ProductParams, task: InventoryPlanningTask) -> ProductParams:
    changes: dict[str, Any] = {}
    o = task.overrides
    if o.service_level is not None:
        changes["service_level"] = o.service_level
    if o.review_period_days is not None:
        changes["review_period_days"] = o.review_period_days
    if o.max_coverage_days is not None:
        changes["max_coverage_days"] = o.max_coverage_days
    if o.lead_time_days is not None:
        changes["lead_time_mean_days"] = o.lead_time_days
    return params.model_copy(update=changes) if changes else params


def _forecast_dict(result: ForecastResult) -> dict[str, Any]:
    return {
        "method": result.method,
        "per_period": result.per_period,
        "values": result.values,
        "params": result.params,
        "sigma": result.sigma,
        "wape": result.wape,
        "mape": result.mape,
        "periods": result.periods,
    }


def _forecast_from(data: dict[str, Any]) -> ForecastResult:
    return ForecastResult(backtests={}, **data)
