"""Exception rules and the action derived from them; explanations leave numbers alone."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from inventory_planning.nodes.detect_exceptions import detect, detect_all, rule_changes
from inventory_planning.nodes.explain import Explanation, explain_lines, explain_run, line_facts
from inventory_planning.policy import ProductParams
from sc_core.infra.settings import LangfuseCfg, Settings, reset_settings_cache
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.planning import ReplenishmentLine
from sc_core.shared.errors import ExternalServiceError

NO_LANGFUSE = LangfuseCfg(enabled=False)
PARAMS = ProductParams.default_for(1, "B")  # max coverage 120 days


def _line(**overrides: Any) -> ReplenishmentLine:
    base: dict[str, Any] = {
        "line_id": "run:1",
        "product_id": 1,
        "product_ref": "CBEA-LHN",
        "product_name": "Contrabalance",
        "warehouse_id": 1,
        "on_hand": 20,
        "reserved": 2,
        "incoming": 10,
        "position": 28,
        "forecast_daily": 1.0,
        "forecast_method": "ses",
        "sigma_daily": 0.8,
        "wape": 0.2,
        "history_periods": 104,
        "lead_time_days": 30,
        "sigma_lead_time_days": 7.5,
        "service_level": 0.95,
        "review_period_days": 7,
        "abc_class": "B",
        "ss": 14.26,
        "rop": 44.26,
        "order_up_to": 51.26,
        "coverage_days": 28.0,
        "current_min": 4,
        "current_max": 56,
        "proposed_min": 45,
        "proposed_max": 52,
        "order_qty": 23.26,
        "supplier_id": 20,
        "supplier_name": "Proveedor Hidraulica",
        "unit_price": 104.0,
        "currency": "PEN",
        "moq": 1,
    }
    return ReplenishmentLine(**{**base, **overrides})


@pytest.mark.parametrize(
    ("overrides", "exception", "action"),
    [
        ({}, "stockout_risk", "update_rule_and_rfq"),  # 28 < 1.0 x 30 and the rule moved
        ({"current_min": 45, "current_max": 52}, "stockout_risk", "create_rfq"),
        ({"position": 40, "on_hand": 32, "order_qty": 11.26}, None, "update_rule_and_rfq"),
        ({"position": 60, "on_hand": 52, "order_qty": 0}, None, "update_rule"),
        (
            {"position": 60, "on_hand": 52, "order_qty": 0, "current_min": 45, "current_max": 52},
            None,
            "none",
        ),
        (
            {"supplier_id": None, "supplier_name": None, "order_qty": 0},
            "no_supplier",
            "manual_review",
        ),
        (
            {"history_periods": 3, "forecast_daily": 0, "order_qty": 0},
            "no_history",
            "manual_review",
        ),
        (
            {"position": -5, "on_hand": 0, "reserved": 5, "incoming": 0, "order_qty": 56.26},
            "negative_position",
            "update_rule_and_rfq",
        ),
        (
            {"position": 400, "on_hand": 392, "coverage_days": 400.0, "order_qty": 0},
            "overstock",
            "update_rule",
        ),
        (
            {"position": 400, "on_hand": 392, "coverage_days": 400.0, "order_qty": 5},
            "overstock",
            "update_rule",
        ),
        (
            {
                "current_min": None,
                "current_max": None,
                "position": 60,
                "on_hand": 52,
                "order_qty": 0,
            },
            None,
            "update_rule",
        ),
    ],
)
def test_rules_flag_and_decide(
    overrides: dict[str, Any], exception: str | None, action: str
) -> None:
    line = detect(_line(**overrides), PARAMS)
    assert (line.exception, line.action) == (exception, action)


def test_lead_time_drift_needs_a_measured_lead_time() -> None:
    line = _line(position=60, on_hand=52, order_qty=0, current_min=45, current_max=52)
    measured = PARAMS.model_copy(update={"lead_time_mean_days": 42.0, "source": "measured"})
    assert detect(line, measured, promised_lead_time=30).exception == "lead_time_drift"
    assert detect(line, measured, promised_lead_time=40).exception is None
    assert detect(line, PARAMS, promised_lead_time=30).exception is None


def test_rule_tolerance() -> None:
    assert not rule_changes(_line(current_min=44, current_max=53))  # within 10 percent / 1 unit
    assert rule_changes(_line(current_min=30, current_max=52))
    assert rule_changes(_line(current_min=None, current_max=None))
    assert not rule_changes(
        _line(current_min=None, current_max=None, proposed_max=0, proposed_min=0)
    )


def test_detect_all_uses_each_products_params() -> None:
    lines = [_line(), _line(line_id="run:2", product_id=2, supplier_id=None, order_qty=0)]
    params = {1: PARAMS, 2: ProductParams.default_for(2, "C")}
    out = detect_all(lines, params, {1: 30.0})
    assert [ln.exception for ln in out] == ["stockout_risk", "no_supplier"]


# --- explanations --------------------------------------------------------------------

NUMERIC = {
    name
    for name, field in ReplenishmentLine.model_fields.items()
    if name not in ("explanation", "exception", "action")
}


async def test_explain_fills_only_the_explanation_of_exception_lines() -> None:
    chat = ScriptedChatClient()
    chat.responses.append(
        Explanation(
            product="CBEA-LHN",
            headline="Riesgo de quiebre en CBEA-LHN.",
            reasoning="La posición de 28 unidades no cubre 30 días de demanda a 1 por día.",
            recommended_action=(
                "Aprobar el pedido de 24 unidades; si se ignora, faltará stock en un mes."
            ),
        )
    )
    flagged = detect(_line(), PARAMS)
    plain = detect(
        _line(
            line_id="run:2",
            product_id=2,
            position=60,
            on_hand=52,
            order_qty=0,
            current_min=45,
            current_max=52,
        ),
        PARAMS,
    )
    out = await explain_lines(chat, [flagged, plain], as_of=date(2026, 9, 14), langfuse=NO_LANGFUSE)
    assert len(chat.calls) == 1  # the plain line never reached the model
    assert out[1] == plain and out[1].explanation is None
    assert out[0].explanation is not None and out[0].explanation.startswith("Riesgo de quiebre")
    assert "Acción:" in out[0].explanation
    for name in NUMERIC:  # the guard: nothing but the explanation changed
        assert getattr(out[0], name) == getattr(flagged, name), name
    prompt_text = chat.calls[0].messages[1]["contents"][0]["text"]
    assert "punto de pedido: 44.26" in prompt_text and "cantidad a pedir: 23.26" in prompt_text


async def test_model_failure_leaves_a_placeholder_and_the_numbers() -> None:
    chat = ScriptedChatClient()
    chat.responses.append(ExternalServiceError("down", service="llm"))
    flagged = detect(_line(), PARAMS)
    [out] = await explain_lines(chat, [flagged], as_of=date(2026, 9, 14), langfuse=NO_LANGFUSE)
    assert out.explanation is not None and out.explanation.startswith(
        "stockout_risk: sin explicación"
    )
    assert out.rop == flagged.rop and out.order_qty == flagged.order_qty


async def test_run_summary_uses_totals_and_falls_back_to_facts() -> None:
    chat = ScriptedChatClient()
    chat.responses.append("Se revisaron 2 productos. Se propone una cotización. Nada pendiente.")
    lines = [detect(_line(), PARAMS)]
    totals = {
        "lines": 2.0,
        "rfq_lines": 1.0,
        "rules_changed": 1.0,
        "manual_review": 0.0,
        "rfq_value_PEN": 2419.0,
    }
    text = await explain_run(
        chat, lines, totals, as_of=date(2026, 9, 14), warehouse_code="WH", langfuse=NO_LANGFUSE
    )
    assert text.startswith("Se revisaron")
    facts = chat.calls[0].messages[1]["contents"][0]["text"]
    assert "valor de las cotizaciones (PEN): 2,419" in facts and "CBEA-LHN: stockout_risk" in facts
    chat.responses.append(ExternalServiceError("down", service="llm"))
    fallback = await explain_run(
        chat, lines, totals, as_of=date(2026, 9, 14), warehouse_code="WH", langfuse=NO_LANGFUSE
    )
    assert fallback.startswith("fecha: 2026-09-14")


def test_line_facts_mentions_every_number_the_model_may_use() -> None:
    text = line_facts(detect(_line(), PARAMS))
    for fragment in (
        "stock disponible: 20",
        "plazo de entrega: 30",
        "regla actual: min 4 / max 56",
        "regla propuesta: min 45 / max 52",
        "104 semanas",
    ):
        assert fragment in text, fragment


FIXTURE = Path("tests") / "fixtures" / "llm" / "inventory_planning.json"


@pytest.mark.llm_cassette
async def test_explanations_from_the_recorded_model() -> None:
    from sc_core.llm import get_chat_client

    reset_settings_cache()
    if os.environ.get("SC__LLM__RECORD_MODE") == "record":
        chat = get_chat_client("inventory_planning", settings=Settings())
    elif FIXTURE.exists():
        chat = get_chat_client(
            "inventory_planning", settings=Settings(_env_file=None, llm={"record_mode": "replay"})
        )
    else:
        pytest.skip(f"{FIXTURE} not recorded yet; run `just llm-record`")
    lines = detect_all(
        [
            _line(),
            _line(
                line_id="run:4",
                product_id=4,
                product_ref="990-011-007",
                product_name="Kit de sellos",
                position=400,
                on_hand=392,
                coverage_days=400.0,
                order_qty=0,
                current_min=36,
                current_max=360,
                proposed_min=20,
                proposed_max=40,
            ),
            _line(
                line_id="run:3",
                product_id=3,
                product_ref="LODC-XDN",
                product_name="Elemento lógico",
                supplier_id=None,
                supplier_name=None,
                unit_price=None,
                order_qty=0,
                current_min=None,
                current_max=None,
            ),
        ],
        {1: PARAMS, 4: PARAMS, 3: ProductParams.default_for(3, "C")},
    )
    out = await explain_lines(chat, lines, as_of=date(2026, 9, 14), langfuse=NO_LANGFUSE)
    assert [ln.exception for ln in out] == ["stockout_risk", "overstock", "no_supplier"]
    for before, after in zip(lines, out, strict=True):
        assert after.explanation and len(after.explanation) > 40
        assert json.loads(before.model_dump_json(exclude={"explanation"})) == json.loads(
            after.model_dump_json(exclude={"explanation"})
        )
    totals = {
        "lines": 3.0,
        "rfq_lines": 1.0,
        "rules_changed": 2.0,
        "manual_review": 1.0,
        "rfq_value_PEN": 2419.0,
    }
    summary = await explain_run(
        chat, out, totals, as_of=date(2026, 9, 14), warehouse_code="WH", langfuse=NO_LANGFUSE
    )
    assert len(summary) > 80 and "3" in summary
