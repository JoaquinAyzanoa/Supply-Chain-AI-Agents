"""Tools a model may call from a node.

A ``Tool`` is an async function with a Pydantic argument model. ``ToolBox``
renders the OpenAI function definitions the chat client forwards to the
provider, and executes calls: arguments are validated, the call runs inside
a Langfuse ``tool.<name>`` span with input and output recorded, and any
error becomes a ``ToolError`` result the model can read instead of an
exception that kills the run. Tools here are read-only by contract; writes
happen only in nodes, after an approval.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from pydantic import BaseModel, ValidationError

from sc_core.infra import tracing
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError

ToolFn = Callable[..., Awaitable[Any]]


class ToolCall(StrictModel):
    """A function call as the provider returned it."""

    id: str
    name: str
    arguments: str = "{}"  # JSON text


class ToolError(StrictModel):
    error: str
    code: str = "tool_error"


class Tool:
    def __init__(self, name: str, description: str, args: type[BaseModel], fn: ToolFn) -> None:
        self.name = name
        self.description = description
        self.args = args
        self._fn = fn

    def definition(self) -> dict[str, Any]:
        """OpenAI chat-completions function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args.model_json_schema(),
            },
        }

    async def __call__(self, **kwargs: Any) -> Any:
        return await self._fn(**kwargs)


def tool(name: str, description: str, args: type[BaseModel]) -> Callable[[ToolFn], Tool]:
    """Decorator: ``@tool("get_po_lines", "...", GetPoLinesArgs)`` over an async function."""

    def wrap(fn: ToolFn) -> Tool:
        return Tool(name, description, args, fn)

    return wrap


class ToolBox:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.add(t)

    def add(self, t: Tool) -> None:
        if t.name in self._tools:
            raise ValueError(f"tool {t.name!r} registered twice")
        self._tools[t.name] = t

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self._tools.values()]

    async def call(self, call: ToolCall) -> Any:
        """Run one call; the result is JSON-serialisable (model dump, list or ``ToolError``)."""
        with tracing.tool_span(call.name, input=_parse_json(call.arguments)) as span:
            result = await self._execute(call)
            span.update(output=_jsonable(result))
        return result

    async def _execute(self, call: ToolCall) -> Any:
        t = self._tools.get(call.name)
        if t is None:
            return ToolError(error=f"unknown tool {call.name!r}", code="unknown_tool")
        try:
            args = t.args.model_validate_json(call.arguments or "{}")
        except ValidationError as exc:
            return ToolError(error=f"invalid arguments: {exc.errors()}", code="invalid_arguments")
        try:
            return await t(**args.model_dump())
        except ScError as exc:
            logger.bind(tool=call.name).warning("tool failed: {}", exc.message)
            return ToolError(error=exc.message, code=exc.code)

    @staticmethod
    def result_text(result: Any) -> str:
        """What goes back to the model as the tool message."""
        return json.dumps(_jsonable(result), ensure_ascii=False, default=str)


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        return {"raw": text}


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value
