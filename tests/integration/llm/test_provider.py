"""Capability checks against the real default provider (DeepSeek unless reassigned).

These decide the values in ``models.yaml``: if one fails, the capability
flag must change, not the test.
"""

from __future__ import annotations

import json

import openai
import pytest
from pydantic import BaseModel, Field

from sc_core.infra.settings import Settings
from sc_core.llm import complete_structured, system, user
from sc_core.llm.client import ChatCompleter
from sc_core.llm.registry import Registry

pytestmark = [pytest.mark.integration, pytest.mark.llm]


class EtaAnswer(BaseModel):
    eta_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    confidence: float = Field(ge=0, le=1)
    reasoning: str


async def test_hello_completion_reports_usage(chat: ChatCompleter) -> None:
    result = await chat.complete(
        [system("Responde en una sola palabra."), user("¿Capital de Perú?")],
        temperature=0,
        max_tokens=20,
    )
    assert "lima" in result.text.lower()
    assert result.usage.input_tokens > 0 and result.usage.output_tokens > 0
    assert result.cost_usd > 0 and result.duration_ms > 0


async def test_structured_output_matches_schema(chat: ChatCompleter) -> None:
    answer = await complete_structured(
        chat,
        [
            system("Extrae la fecha de entrega del correo del proveedor. Hoy es 2026-09-12."),
            user("Confirmamos que la orden llega el 20 de octubre de 2026. Saludos."),
        ],
        EtaAnswer,
    )
    assert answer.eta_date == "2026-10-20"
    assert answer.confidence >= 0.7


async def test_parallel_tool_calls_capability(llm_settings: Settings) -> None:
    """Asks for two independent lookups and checks both tool calls arrive in one turn.

    Uses the OpenAI SDK directly on purpose: the framework would auto-invoke
    tools and hide whether the provider emitted them in parallel.
    """
    registry = Registry.load()
    spec = registry.model(llm_settings.llm.default_model)
    provider = registry.provider(spec.provider)
    client = openai.AsyncOpenAI(api_key=provider.api_key(), base_url=provider.base_url)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_purchase_order",
                "description": "Datos de una orden de compra por nombre",
                "parameters": {
                    "type": "object",
                    "properties": {"po_name": {"type": "string"}},
                    "required": ["po_name"],
                },
            },
        }
    ]
    response = await client.chat.completions.create(
        model=spec.name,
        temperature=0,
        messages=[
            {"role": "system", "content": "Usa la herramienta para cada orden mencionada."},
            {"role": "user", "content": "Necesito los datos de las órdenes P00015 y P00016."},
        ],
        tools=tools,  # type: ignore[arg-type]
        tool_choice="auto",
        parallel_tool_calls=spec.parallel_tool_calls,
    )
    calls = response.choices[0].message.tool_calls or []
    names = sorted(json.loads(c.function.arguments)["po_name"] for c in calls)
    assert names == ["P00015", "P00016"], (
        f"expected two parallel tool calls, got {len(calls)}; "
        f"set parallel_tool_calls accordingly in models.yaml for {spec.name}"
    )


async def test_json_schema_capability(llm_settings: Settings) -> None:
    """Strict json_schema response_format is accepted and honoured by the provider."""
    registry = Registry.load()
    spec = registry.model(llm_settings.llm.default_model)
    provider = registry.provider(spec.provider)
    client = openai.AsyncOpenAI(api_key=provider.api_key(), base_url=provider.base_url)
    response = await client.chat.completions.create(
        model=spec.name,
        temperature=0,
        messages=[{"role": "user", "content": "Devuelve eta_date 2026-10-20 con confidence 0.9."}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "eta",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "eta_date": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["eta_date", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    assert payload == {"eta_date": "2026-10-20", "confidence": 0.9}, (
        f"json_schema not honoured by {spec.name}; set json_schema_output accordingly"
    )
