"""TracedChatClient: options, tracing payloads, budget and error translation."""

from __future__ import annotations

from typing import Any

import httpx
import openai
import pytest
from pydantic import BaseModel

from sc_core.llm.budget import RunBudget, current_budget
from sc_core.llm.client import TracedChatClient, message_to_dict, system, to_message, user
from sc_core.llm.testing import FAKE_SPEC
from sc_core.shared.errors import BudgetExceeded, ExternalServiceError

from .fake_inner import FakeInner, response


class Answer(BaseModel):
    eta: str


def make_client(script: list[Any]) -> tuple[TracedChatClient, FakeInner]:
    inner = FakeInner(script)
    client = TracedChatClient(inner, spec=FAKE_SPEC, agent_name="tester", provider_name="fake")  # type: ignore[arg-type]
    return client, inner


def test_message_helpers() -> None:
    assert system("s").role == "system" and user("u").text == "u"
    assert to_message({"role": "assistant", "content": "hola"}).text == "hola"
    d = message_to_dict(user("hola"))
    assert d["role"] == "user" and d["contents"][0] == {
        "type": "text",
        "text": "hola",
        "additional_properties": {},
    }


async def test_complete_returns_result_with_usage_and_cost() -> None:
    client, inner = make_client(
        [response("La fecha es 2026-10-20", input_tokens=1000, output_tokens=500)]
    )
    result = await client.complete([system("s"), user("u")], temperature=0.2, max_tokens=100)
    assert result.text == "La fecha es 2026-10-20"
    assert result.usage.input_tokens == 1000 and result.usage.output_tokens == 500
    assert result.cost_usd == pytest.approx(0.001 + 0.001)  # 1$/M in, 2$/M out
    assert result.finish_reason == "stop" and result.model == "fake-model"
    messages, options = inner.calls[0]
    assert [m.text for m in messages] == ["s", "u"]
    assert options == {"model": "fake-model", "temperature": 0.2, "max_tokens": 100}


async def test_max_tokens_is_capped_and_response_format_forwarded() -> None:
    client, inner = make_client([response('{"eta": "2026-10-20"}')])
    await client.complete([user("u")], max_tokens=1_000_000, response_format=Answer)
    _, options = inner.calls[0]
    assert options["max_tokens"] == FAKE_SPEC.max_output_tokens
    assert options["response_format"] is Answer


async def test_tools_set_parallel_flag() -> None:
    client, inner = make_client([response("ok")])
    tool = {"type": "function", "function": {"name": "get_po"}}
    await client.complete([user("u")], tools=[tool])
    _, options = inner.calls[0]
    assert options["tools"] == [tool] and options["allow_multiple_tool_calls"] is True


async def test_budget_is_checked_before_and_recorded_after() -> None:
    client, _ = make_client([response("a", input_tokens=50, output_tokens=10), response("b")])
    budget = RunBudget(max_input_tokens=60, max_output_tokens=100, max_usd=1.0)
    token = current_budget.set(budget)
    try:
        await client.complete([user("u")])
        assert budget.calls == 1 and budget.input_tokens == 50
        await client.complete([user("u")])  # 62 > 60 after this call ...
        with pytest.raises(BudgetExceeded):
            await client.complete([user("u")])  # ... so the third is refused before sending
    finally:
        current_budget.reset(token)


@pytest.mark.parametrize(
    ("exc", "retryable", "fragment"),
    [
        (
            openai.RateLimitError(
                "slow down",
                response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
                body=None,
            ),
            True,
            "rate limited",
        ),
        (
            openai.InternalServerError(
                "boom",
                response=httpx.Response(503, request=httpx.Request("POST", "http://x")),
                body=None,
            ),
            True,
            "HTTP 503",
        ),
        (
            openai.BadRequestError(
                "bad",
                response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                body=None,
            ),
            False,
            "HTTP 400",
        ),
        (openai.APIConnectionError(request=httpx.Request("POST", "http://x")), True, "unreachable"),
        (RuntimeError("framework exploded"), False, "framework exploded"),
    ],
)
async def test_errors_are_translated(exc: Exception, retryable: bool, fragment: str) -> None:
    client, _ = make_client([exc])
    with pytest.raises(ExternalServiceError) as info:
        await client.complete([user("u")])
    assert info.value.retryable is retryable
    assert fragment in info.value.message
    assert info.value.details["service"] == "llm"
