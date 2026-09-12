"""Test doubles for the chat client.

- ``ScriptedChatClient``: hand-written responses in order; records what it
  was asked. For agent unit tests that assert prompts and control flow.
- Cassettes (``RecordingChatClient`` / ``ReplayChatClient``): real model
  answers captured once with ``just llm-record`` into
  ``tests/fixtures/llm/*.json`` and replayed offline, keyed by a hash of the
  model, messages and options. For tests that need realistic model output.

Both satisfy ``ChatCompleter`` and honour the budget and Langfuse tracing
like the real client would not: doubles are deliberately silent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_framework import Content, Message
from pydantic import BaseModel

from sc_core.llm.client import (
    ChatCompleter,
    ChatResult,
    MessageLike,
    Usage,
    message_to_dict,
    to_message,
)
from sc_core.llm.registry import ModelSpec, Price
from sc_core.shared.errors import ExternalServiceError

FAKE_SPEC = ModelSpec(
    name="fake-model",
    provider="fake",
    context_window=100_000,
    max_output_tokens=8_000,
    parallel_tool_calls=True,
    json_schema_output=True,
    price_per_mtok=Price(input=1.0, output=2.0),
)


def _result(
    text: str, spec: ModelSpec, *, input_tokens: int = 10, output_tokens: int = 5
) -> ChatResult:
    return ChatResult(
        text=text,
        messages=[
            {"type": "message", "role": "assistant", "contents": [{"type": "text", "text": text}]}
        ],
        finish_reason="stop",
        model=spec.name,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        cost_usd=spec.cost(input_tokens, output_tokens)["total"],
        duration_ms=1.0,
    )


@dataclass
class Call:
    messages: list[dict[str, Any]]
    options: dict[str, Any]


@dataclass
class ScriptedChatClient:
    """Answers with ``responses`` in order (a ``BaseModel`` is serialised to JSON)."""

    responses: list[str | BaseModel | ChatResult | Exception] = field(default_factory=list)
    spec: ModelSpec = FAKE_SPEC
    calls: list[Call] = field(default_factory=list)

    async def complete(
        self,
        messages: Sequence[MessageLike],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: type[BaseModel] | dict[str, Any] | None = None,
        tools: Sequence[Any] | None = None,
        metadata: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> ChatResult:
        self.calls.append(
            Call(
                messages=[message_to_dict(to_message(m)) for m in messages],
                options={
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "response_format": getattr(response_format, "__name__", response_format),
                    "tools": [getattr(t, "name", str(t)) for t in tools or []],
                    "name": name,
                },
            )
        )
        if not self.responses:
            raise AssertionError(
                f"ScriptedChatClient has no response left for call {len(self.calls)}"
            )
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ChatResult):
            return item
        text = item.model_dump_json() if isinstance(item, BaseModel) else item
        return _result(text, self.spec)

    def last_prompt_text(self) -> str:
        """All text of the last call's messages, for assertions on prompt content."""
        parts: list[str] = []
        for message in self.calls[-1].messages:
            for content in message.get("contents", []):
                if content.get("type") == "text":
                    parts.append(content["text"])
        return "\n".join(parts)


# --- cassettes ---------------------------------------------------------------------


def request_key(model: str, messages: list[dict[str, Any]], options: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"model": model, "messages": messages, "options": options},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _options_key(**kwargs: Any) -> dict[str, Any]:
    fmt = kwargs.get("response_format")
    return {
        "temperature": kwargs.get("temperature"),
        "max_tokens": kwargs.get("max_tokens"),
        "response_format": getattr(fmt, "__name__", fmt),
        "tools": [getattr(t, "name", str(t)) for t in kwargs.get("tools") or []],
    }


@dataclass
class ChatCassette:
    path: Path
    entries: list[dict[str, Any]] = field(default_factory=list)
    _cursor: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ChatCassette:
        if not path.exists():
            raise FileNotFoundError(f"cassette {path} not found; record with `just llm-record`")
        return cls(path=path, entries=list(json.loads(path.read_text(encoding="utf-8"))["entries"]))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"entries": self.entries}, indent=1, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )

    def record(self, key: str, request: dict[str, Any], result: ChatResult) -> None:
        self.entries.append({"key": key, "request": request, "result": result.model_dump()})

    def replay(self, key: str, request: dict[str, Any]) -> ChatResult:
        matches = [e for e in self.entries if e["key"] == key]
        if not matches:
            raise AssertionError(
                f"no recorded answer in {self.path.name} for model {request['model']} "
                f"({len(request['messages'])} messages); re-record with `just llm-record`"
            )
        index = min(self._cursor.get(key, 0), len(matches) - 1)
        self._cursor[key] = index + 1
        return ChatResult.model_validate(matches[index]["result"])


class RecordingChatClient:
    def __init__(self, inner: ChatCompleter, cassette: ChatCassette) -> None:
        self._inner, self._cassette = inner, cassette
        self.spec = inner.spec

    async def complete(self, messages: Sequence[MessageLike], **kwargs: Any) -> ChatResult:
        request: dict[str, Any] = {
            "model": self.spec.name,
            "messages": [message_to_dict(to_message(m)) for m in messages],
            "options": _options_key(**kwargs),
        }
        result = await self._inner.complete(messages, **kwargs)
        self._cassette.record(
            request_key(request["model"], request["messages"], request["options"]), request, result
        )
        self._cassette.save()
        return result


class ReplayChatClient:
    def __init__(self, cassette: ChatCassette, spec: ModelSpec) -> None:
        self._cassette, self.spec = cassette, spec

    async def complete(self, messages: Sequence[MessageLike], **kwargs: Any) -> ChatResult:
        request: dict[str, Any] = {
            "model": self.spec.name,
            "messages": [message_to_dict(to_message(m)) for m in messages],
            "options": _options_key(**kwargs),
        }
        return self._cassette.replay(
            request_key(request["model"], request["messages"], request["options"]), request
        )


class FailingChatClient:
    """Every call fails the same way; for retry and escalation tests."""

    def __init__(self, error: Exception | None = None, spec: ModelSpec = FAKE_SPEC) -> None:
        self._error = error or ExternalServiceError("llm down", service="llm")
        self.spec = spec

    async def complete(self, messages: Sequence[MessageLike], **kwargs: Any) -> ChatResult:
        raise self._error


def tool_call_result(
    name: str,
    arguments: dict[str, Any] | str,
    *,
    call_id: str = "call_1",
    spec: ModelSpec = FAKE_SPEC,
) -> ChatResult:
    """A scripted response in which the model asks for one tool call."""
    args = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    message = Message(
        "assistant", [Content.from_function_call(call_id=call_id, name=name, arguments=args)]
    )
    return ChatResult(
        text="",
        messages=[message_to_dict(message)],
        finish_reason="tool_calls",
        model=spec.name,
        usage=Usage(input_tokens=10, output_tokens=5),
        cost_usd=0.0,
        duration_ms=1.0,
    )
