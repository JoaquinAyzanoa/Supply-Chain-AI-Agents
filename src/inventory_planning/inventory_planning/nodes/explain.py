"""The model explains; it never computes.

One structured call per exception line (headline, reasoning, recommended
action, in Spanish for the approver) and one call for the run summary.
Lines without an exception are not sent to the model. The only field the
model may change on a line is ``explanation``; a guard test asserts every
number is untouched.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from loguru import logger
from pydantic import Field

from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from sc_core.schema.base import StrictModel
from sc_core.schema.planning import ReplenishmentLine
from sc_core.shared.errors import ScError

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
AGENT_NAME = "inventory_planning"


class Explanation(StrictModel):
    product: str
    headline: str = Field(max_length=200)
    reasoning: str = Field(max_length=800)
    recommended_action: str = Field(max_length=300)

    def render(self) -> str:
        return f"{self.headline} {self.reasoning} Acción: {self.recommended_action}"


def line_facts(line: ReplenishmentLine) -> str:
    """The numbers the model may talk about, as plain text."""
    rows = [
        f"producto: {line.product_ref} {line.product_name}".strip(),
        f"excepción: {line.exception}",
        f"acción propuesta por las reglas: {line.action}",
        f"stock disponible: {line.on_hand:g} (reservado {line.reserved:g}), "
        f"por recibir: {line.incoming:g}, posición: {line.position:g}",
        f"demanda diaria prevista: {line.forecast_daily:g} ({line.forecast_method}, "
        f"{line.history_periods} semanas de historia"
        + (f", WAPE {line.wape:.0%}" if line.wape is not None else "")
        + ")",
        f"plazo de entrega: {line.lead_time_days:g} días (sigma {line.sigma_lead_time_days:g})",
        f"nivel de servicio: {line.service_level:.0%}, clase {line.abc_class}",
        f"stock de seguridad: {line.ss:g}, punto de pedido: {line.rop:g}, "
        f"nivel máximo: {line.order_up_to:g}",
        f"cobertura actual: {line.coverage_days:g} días"
        if line.coverage_days is not None
        else "cobertura actual: sin demanda",
        f"regla actual: min {line.current_min:g} / max {line.current_max:g}"
        if line.current_min is not None and line.current_max is not None
        else "regla actual: ninguna",
        f"regla propuesta: min {line.proposed_min:g} / max {line.proposed_max:g}",
        f"cantidad a pedir: {line.order_qty:g}"
        + (f" a {line.supplier_name}" if line.supplier_name else "")
        + (f" ({line.unit_price:g} {line.currency or ''} c/u)" if line.unit_price else ""),
    ]
    return "\n".join(f"- {row}" for row in rows)


async def explain_line(
    chat: ChatCompleter, line: ReplenishmentLine, *, as_of: date, langfuse: LangfuseCfg | None
) -> ReplenishmentLine:
    """The same line with ``explanation`` filled; numbers are copied, never rewritten."""
    if line.exception is None:
        return line
    prompt = get_prompt("explain_exception", local_dir=PROMPTS_DIR, cfg=langfuse)
    try:
        result = await complete_structured(
            chat,
            [system(prompt.compile(as_of=as_of.isoformat())), user(line_facts(line))],
            Explanation,
            name="inventory_planning.explain",
            metadata={
                "prompt": prompt.name,
                "prompt_version": prompt.version,
                "product": line.product_ref,
                "exception": line.exception,
            },
        )
    except ScError as exc:
        logger.bind(product=line.product_ref).warning("explanation unavailable: {}", exc)
        return line.model_copy(
            update={"explanation": f"{line.exception}: sin explicación ({exc.message})"[:500]}
        )
    return line.model_copy(update={"explanation": result.render()[:1000]})


async def explain_lines(
    chat: ChatCompleter,
    lines: list[ReplenishmentLine],
    *,
    as_of: date,
    langfuse: LangfuseCfg | None,
) -> list[ReplenishmentLine]:
    return [await explain_line(chat, line, as_of=as_of, langfuse=langfuse) for line in lines]


async def explain_run(
    chat: ChatCompleter,
    lines: list[ReplenishmentLine],
    totals: dict[str, float],
    *,
    as_of: date,
    warehouse_code: str,
    langfuse: LangfuseCfg | None,
) -> str:
    """A short narrative of the run for the approval note."""
    prompt = get_prompt("explain_run", local_dir=PROMPTS_DIR, cfg=langfuse)
    exceptions = [ln for ln in lines if ln.exception]
    facts = [
        f"fecha: {as_of.isoformat()}, almacén: {warehouse_code}",
        f"productos revisados: {int(totals.get('lines', len(lines)))}",
        f"solicitudes de cotización propuestas: {int(totals.get('rfq_lines', 0))}",
        f"reglas de reposición a cambiar: {int(totals.get('rules_changed', 0))}",
        f"excepciones: {len(exceptions)}",
        f"pendientes de revisión manual: {int(totals.get('manual_review', 0))}",
    ]
    for key, value in sorted(totals.items()):
        if key.startswith("rfq_value_"):
            facts.append(
                f"valor de las cotizaciones ({key.removeprefix('rfq_value_')}): {value:,.0f}"
            )
    for line in exceptions:
        facts.append(f"- {line.product_ref}: {line.exception} → {line.action}")
    try:
        result = await chat.complete(
            [system(prompt.text), user("\n".join(facts))],
            temperature=0.2,
            max_tokens=400,
            name="inventory_planning.summary",
            metadata={"prompt": prompt.name, "prompt_version": prompt.version},
        )
    except ScError as exc:
        logger.warning("run summary unavailable: {}", exc)
        return "; ".join(facts[:6])
    return " ".join(result.text.split())[:1500] or "; ".join(facts[:6])
