"""complete_structured with scripted answers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from sc_core.llm.registry import Price
from sc_core.llm.structured import (
    StructuredOutputFailed,
    complete_structured,
    parse_as,
    strip_fences,
)
from sc_core.llm.testing import FAKE_SPEC, ScriptedChatClient


class Eta(BaseModel):
    eta_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    confidence: float = Field(ge=0, le=1)


def test_strip_fences_and_parse() -> None:
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('{"a": 1}') == '{"a": 1}'
    assert (
        parse_as(Eta, '```json\n{"eta_date": "2026-10-20", "confidence": 0.9}\n```').confidence
        == 0.9
    )


async def test_valid_first_try_uses_schema_format() -> None:
    client = ScriptedChatClient([Eta(eta_date="2026-10-20", confidence=0.8)])
    result = await complete_structured(client, [{"role": "user", "content": "¿cuándo llega?"}], Eta)
    assert result.eta_date == "2026-10-20"
    call = client.calls[0]
    assert call.options["response_format"] == "Eta" and call.options["temperature"] == 0.0
    assert len(call.messages) == 1  # no schema instruction appended in json_schema mode


async def test_invalid_then_valid_retries_with_error_context() -> None:
    client = ScriptedChatClient(
        [
            '{"eta_date": "20/10", "confidence": 0.8}',
            '{"eta_date": "2026-10-20", "confidence": 0.8}',
        ]
    )
    result = await complete_structured(client, [{"role": "user", "content": "q"}], Eta)
    assert result.eta_date == "2026-10-20"
    assert len(client.calls) == 2
    second = client.last_prompt_text()
    assert "20/10" in second and "not valid" in second and "Error:" in second


async def test_two_failures_raise_with_both_errors() -> None:
    client = ScriptedChatClient(["not json", '{"eta_date": "x", "confidence": 5}'])
    with pytest.raises(StructuredOutputFailed) as exc:
        await complete_structured(client, [{"role": "user", "content": "q"}], Eta)
    assert len(exc.value.details["errors"]) == 2 and exc.value.details["schema"] == "Eta"


async def test_json_object_mode_when_schema_unsupported() -> None:
    spec = FAKE_SPEC.model_copy(
        update={"json_schema_output": False, "price_per_mtok": Price(input=0, output=0)}
    )
    client = ScriptedChatClient(['{"eta_date": "2026-10-20", "confidence": 1}'], spec=spec)
    await complete_structured(client, [{"role": "user", "content": "q"}], Eta)
    call = client.calls[0]
    assert call.options["response_format"] == {"type": "json_object"}
    assert "schema" in client.last_prompt_text() and "eta_date" in client.last_prompt_text()
