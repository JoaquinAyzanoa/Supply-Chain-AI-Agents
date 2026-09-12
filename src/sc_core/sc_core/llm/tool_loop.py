"""The model <-> tools loop, run by us rather than by the framework.

Each round is one traced generation. When the model asks for tools, every
call runs through the ``ToolBox`` (validated, traced, errors as results),
the results go back as tool messages and the model is asked again, up to
``max_rounds``. The loop ends when the model answers with text. Callers
usually follow it with ``complete_structured`` over the same history to get
a typed answer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent_framework import Content, Message
from loguru import logger

from sc_core.graph.tools import ToolBox, ToolCall
from sc_core.llm.client import ChatCompleter, ChatResult, MessageLike, to_message
from sc_core.schema.base import StrictModel


class ToolLoopResult(StrictModel):
    """``history`` is the full conversation (as message dicts) including tool exchanges."""

    history: list[dict[str, Any]]
    final: ChatResult
    rounds: int
    tool_calls: int

    def messages(self) -> list[Message]:
        return [Message.from_dict(m) for m in self.history]


async def run_tool_loop(
    chat: ChatCompleter,
    messages: Sequence[MessageLike],
    toolbox: ToolBox,
    *,
    max_rounds: int = 6,
    temperature: float | None = 0.2,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ToolLoopResult:
    history: list[Message] = [to_message(m) for m in messages]
    definitions = toolbox.definitions()
    calls_total = 0
    result: ChatResult | None = None
    for round_no in range(1, max_rounds + 1):
        result = await chat.complete(
            history,
            tools=definitions,
            temperature=temperature,
            name=f"{name or 'tool_loop'}.{round_no}",
            metadata=metadata,
        )
        history.extend(Message.from_dict(m) for m in result.messages)
        calls = result.tool_calls
        if not calls:
            return ToolLoopResult(
                history=[m.to_dict() for m in history],
                final=result,
                rounds=round_no,
                tool_calls=calls_total,
            )
        for call in calls:
            calls_total += 1
            tool_call = ToolCall(id=call["call_id"], name=call["name"], arguments=call["arguments"])
            outcome = await toolbox.call(tool_call)
            history.append(
                Message(
                    "tool",
                    [
                        Content.from_function_result(
                            call_id=call["call_id"], result=ToolBox.result_text(outcome)
                        )
                    ],
                )
            )
    logger.warning("tool loop hit max_rounds={}; answering with the last response", max_rounds)
    assert result is not None
    history.append(Message("user", [_STOP]))
    result = await chat.complete(
        history, temperature=temperature, name=f"{name or 'tool_loop'}.final"
    )
    history.extend(Message.from_dict(m) for m in result.messages)
    return ToolLoopResult(
        history=[m.to_dict() for m in history],
        final=result,
        rounds=max_rounds + 1,
        tool_calls=calls_total,
    )


_STOP = "No more tool calls are available. Answer now with the information you already have."


def tool_exchange_summary(history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calls and results in order, for logs and result summaries."""
    out: list[dict[str, Any]] = []
    for m in history:
        for c in m.get("contents", []):
            if c.get("type") == "function_call":
                out.append({"call": c["name"], "arguments": _short(c.get("arguments"))})
            elif c.get("type") == "function_result":
                out.append({"result": _short(c.get("result"))})
    return out


def _short(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"
