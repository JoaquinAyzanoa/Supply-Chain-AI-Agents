"""A stand-in for Microsoft Agent Framework's chat client: scripted ChatResponse objects."""

from __future__ import annotations

from typing import Any

from agent_framework import ChatResponse, Message, UsageDetails


def response(
    text: str, *, input_tokens: int = 12, output_tokens: int = 7, finish: str = "stop"
) -> ChatResponse[Any]:
    return ChatResponse(
        messages=[Message("assistant", [text])],
        usage_details=UsageDetails(
            input_token_count=input_tokens, output_token_count=output_tokens
        ),
        model="fake-model",
        finish_reason=finish,
    )


class FakeInner:
    def __init__(self, script: list[ChatResponse[Any] | Exception]) -> None:
        self.script = list(script)
        self.calls: list[tuple[list[Message], dict[str, Any]]] = []

    async def get_response(
        self, messages: list[Message], *, options: dict[str, Any]
    ) -> ChatResponse[Any]:
        self.calls.append((list(messages), dict(options)))
        if not self.script:
            raise AssertionError("FakeInner has no scripted response left")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
