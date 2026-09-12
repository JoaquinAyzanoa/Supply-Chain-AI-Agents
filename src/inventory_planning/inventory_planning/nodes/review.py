"""``review_product``: one product, plus the context only text holds.

The rules have already produced the numbers. The model reads the product's
internal notes and the reason the review was asked (an orderpoint trigger,
a planner's remark, a supplier saying a part is discontinued) and picks one
of a fixed set of actions: ``keep`` the rule output, ``hold`` (do not
order), ``switch_supplier`` (recompute with the next supplier) or
``manual_review``. It never proposes a quantity; a switch recomputes the
line with the formulas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import Field

from inventory_planning.forecasting import ForecastResult
from inventory_planning.models import ProductData
from inventory_planning.nodes.compute_policy import compute_line
from inventory_planning.nodes.detect_exceptions import detect
from inventory_planning.nodes.explain import line_facts
from inventory_planning.nodes.propose import dataset_of, lines_of, params_of, task_of
from inventory_planning.state import Node
from sc_core.infra.settings import LangfuseCfg, PlanningCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from sc_core.schema.base import StrictModel
from sc_core.schema.planning import ReplenishmentLine
from sc_core.shared.errors import ScError

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
ReviewAction = Literal["keep", "hold", "switch_supplier", "manual_review"]


class ReviewDecision(StrictModel):
    action: ReviewAction
    reason: str = Field(max_length=600)


def make_review(chat: ChatCompleter, cfg: PlanningCfg, *, langfuse: LangfuseCfg | None) -> Node:
    async def review(state: Any) -> dict[str, Any]:
        task = task_of(state)
        if task.kind != "review_product":
            return {}
        dataset = dataset_of(state)
        params = params_of(state)
        out: list[dict[str, Any]] = []
        for line in lines_of(state):
            product = dataset.product(line.product_id)
            if product is None:
                out.append(line.model_dump(mode="json"))
                continue
            decision = await _decide(chat, line, product, task.context, langfuse)
            reviewed = _apply_decision(line, decision, product, state, params, cfg)
            logger.bind(product=line.product_ref, action=decision.action).info("product reviewed")
            out.append(reviewed.model_dump(mode="json"))
        return {"lines": out}

    return review


async def _decide(
    chat: ChatCompleter,
    line: ReplenishmentLine,
    product: ProductData,
    context: str | None,
    langfuse: LangfuseCfg | None,
) -> ReviewDecision:
    prompt = get_prompt("review_product", local_dir=PROMPTS_DIR, cfg=langfuse)
    alternates = [t.partner_name for t in product.suppliers[1:]]
    facts = "\n".join(
        [
            line_facts(line),
            f"- notas internas del producto: {product.notes or '(ninguna)'}",
            f"- motivo de la revisión: {context or '(no indicado)'}",
            f"- proveedores alternativos: {', '.join(alternates) or 'ninguno'}",
        ]
    )
    try:
        return await complete_structured(
            chat,
            [system(prompt.text), user(facts)],
            ReviewDecision,
            name="inventory_planning.review",
            metadata={"prompt": prompt.name, "prompt_version": prompt.version},
        )
    except ScError as exc:
        logger.bind(product=line.product_ref).warning("review unavailable: {}", exc)
        return ReviewDecision(
            action="manual_review", reason=f"sin revisión del modelo: {exc.message}"
        )


def _apply_decision(
    line: ReplenishmentLine,
    decision: ReviewDecision,
    product: ProductData,
    state: dict[str, Any],
    params: dict[str, Any],
    cfg: PlanningCfg,
) -> ReplenishmentLine:
    note = f"Revisión: {decision.reason}"
    if decision.action == "keep":
        return line
    if decision.action == "hold":
        return line.model_copy(update={"action": "hold", "order_qty": 0.0, "explanation": note})
    if decision.action == "switch_supplier" and len(product.suppliers) > 1:
        forecast = ForecastResult(backtests={}, **state["forecasts"][str(line.product_id)])
        alternate = product.model_copy(update={"suppliers": product.suppliers[1:]})
        recomputed = compute_line(
            alternate,
            forecast,
            params[line.product_id],
            run_id=state["run_id"],
            warehouse_id=line.warehouse_id,
            lead_time_sigma_ratio=cfg.lead_time_sigma_ratio,
        )
        flagged = detect(recomputed, params[line.product_id])
        return flagged.model_copy(update={"explanation": note})
    return line.model_copy(update={"action": "manual_review", "explanation": note})
