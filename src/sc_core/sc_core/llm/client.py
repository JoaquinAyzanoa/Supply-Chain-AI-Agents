"""The one chat client every agent uses.

``TracedChatClient`` wraps Microsoft Agent Framework's OpenAI chat-completions
client (which speaks to DeepSeek, OpenAI and any compatible server through
``base_url``) and adds what the project requires of every model call:

- a Langfuse generation with the complete input (system prompt, messages,
  tools, response format, parameters) and output (messages, finish reason),
  usage and cost
- the per-run budget check before, and the usage recorded after
- error translation into ``ExternalServiceError`` with a retryable flag

Agents type their dependency as ``ChatCompleter`` so tests can substitute a
scripted or replayed client (``sc_core.llm.testing``).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, Protocol, cast

import openai
from agent_framework import ChatResponse, Message
from agent_framework.openai import OpenAIChatCompletionClient
from loguru import logger
from pydantic import BaseModel

from sc_core.infra.settings import LlmCfg
from sc_core.infra.tracing import tracer
from sc_core.llm.budget import get_budget
from sc_core.llm.registry import ModelSpec, ProviderSpec, Registry
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ExternalServiceError

SERVICE = "llm"

MessageLike = Message | dict[str, Any]


class Usage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class ChatResult(StrictModel):
    """What a completion returns to the caller. ``messages`` are Graph-neutral dicts."""

    text: str
    messages: list[dict[str, Any]]
    finish_reason: str | None
    model: str
    usage: Usage
    cost_usd: float
    duration_ms: float


class ChatCompleter(Protocol):
    spec: ModelSpec

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
    ) -> ChatResult: ...


# --- message helpers -------------------------------------------------------------


def system(text: str) -> Message:
    return Message("system", [text])


def user(text: str) -> Message:
    return Message("user", [text])


def assistant(text: str) -> Message:
    return Message("assistant", [text])


def to_message(item: MessageLike) -> Message:
    if isinstance(item, Message):
        return item
    role = item["role"]
    content = item.get("content", "")
    return Message(role, [content] if isinstance(content, str) else list(content))


def message_to_dict(message: Message) -> dict[str, Any]:
    """Serialisable, provider-neutral form used for traces and fixtures."""
    return message.to_dict()


# --- the client ----------------------------------------------------------------------


class TracedChatClient:
    def __init__(
        self,
        inner: OpenAIChatCompletionClient,
        *,
        spec: ModelSpec,
        agent_name: str,
        provider_name: str,
    ) -> None:
        self._inner = inner
        self.spec = spec
        self.agent_name = agent_name
        self.provider_name = provider_name

    @classmethod
    def build(
        cls, agent_name: str, *, cfg: LlmCfg, registry: Registry | None = None
    ) -> TracedChatClient:
        registry = registry or Registry.load()
        model_name = cfg.model_for(agent_name)
        spec = registry.model(model_name)
        provider = registry.provider(spec.provider)
        return cls(
            _make_inner(spec, provider, cfg),
            spec=spec,
            agent_name=agent_name,
            provider_name=provider.name,
        )

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
        budget = get_budget()
        if budget is not None:
            budget.check()

        maf_messages = [to_message(m) for m in messages]
        options: dict[str, Any] = {"model": self.spec.name}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["max_tokens"] = min(max_tokens, self.spec.max_output_tokens)
        if response_format is not None:
            options["response_format"] = response_format
        if tools:
            options["tools"] = list(tools)
            options["allow_multiple_tool_calls"] = self.spec.parallel_tool_calls

        trace_input = {
            "messages": [message_to_dict(m) for m in maf_messages],
            "tools": _describe_tools(tools),
            "response_format": _describe_format(response_format),
        }
        model_parameters = {
            k: v for k, v in options.items() if k in ("temperature", "max_tokens", "model")
        }
        started = time.perf_counter()
        with tracer().start_as_current_observation(
            name=name or f"{self.agent_name}.chat",
            as_type="generation",
            model=self.spec.name,
            input=trace_input,
            model_parameters=model_parameters,
            metadata={"provider": self.provider_name, "agent": self.agent_name, **(metadata or {})},
        ) as generation:
            try:
                response: ChatResponse[Any] = await self._inner.get_response(
                    maf_messages, options=cast(Any, options)
                )
            except Exception as exc:
                error = _translate(exc)
                generation.update(level="ERROR", status_message=error.message)
                raise error from exc
            result = _to_result(response, self.spec, time.perf_counter() - started)
            generation.update(
                output={"messages": result.messages, "finish_reason": result.finish_reason},
                usage_details={
                    "input": result.usage.input_tokens,
                    "output": result.usage.output_tokens,
                },
                cost_details=self.spec.cost(result.usage.input_tokens, result.usage.output_tokens),
            )
        if budget is not None:
            budget.record(
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                usd=result.cost_usd,
            )
        logger.bind(
            model=self.spec.name,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cost_usd=round(result.cost_usd, 6),
            duration_ms=round(result.duration_ms),
        ).info("llm call")
        return result

    async def aclose(self) -> None:
        client = getattr(self._inner, "client", None)
        if client is not None and hasattr(client, "close"):
            await client.close()


# --- helpers --------------------------------------------------------------------------


def _make_inner(spec: ModelSpec, provider: ProviderSpec, cfg: LlmCfg) -> OpenAIChatCompletionClient:
    async_client = openai.AsyncOpenAI(
        api_key=provider.api_key(),
        base_url=provider.base_url,
        timeout=cfg.timeout_seconds,
        max_retries=cfg.max_retries,
    )
    return OpenAIChatCompletionClient(model=spec.name, async_client=async_client)


def _to_result(response: ChatResponse[Any], spec: ModelSpec, seconds: float) -> ChatResult:
    usage_details = response.usage_details or {}
    usage = Usage(
        input_tokens=int(usage_details.get("input_token_count") or 0),
        output_tokens=int(usage_details.get("output_token_count") or 0),
    )
    finish = response.finish_reason
    return ChatResult(
        text=response.text or "",
        messages=[message_to_dict(m) for m in response.messages],
        finish_reason=str(finish) if finish is not None else None,
        model=response.model or spec.name,
        usage=usage,
        cost_usd=spec.cost(usage.input_tokens, usage.output_tokens)["total"],
        duration_ms=seconds * 1000,
    )


def _describe_tools(tools: Sequence[Any] | None) -> list[Any]:
    described: list[Any] = []
    for tool in tools or []:
        spec_fn = getattr(tool, "to_json_schema_spec", None)
        if callable(spec_fn):
            described.append(spec_fn())
        elif isinstance(tool, dict):
            described.append(tool)
        else:
            described.append({"name": getattr(tool, "name", repr(tool))})
    return described


def _describe_format(response_format: type[BaseModel] | dict[str, Any] | None) -> Any:
    if response_format is None:
        return None
    if isinstance(response_format, dict):
        return response_format
    return {
        "type": "json_schema",
        "name": response_format.__name__,
        "schema": response_format.model_json_schema(),
    }


def _translate(exc: Exception) -> ExternalServiceError:
    if isinstance(exc, openai.RateLimitError):
        return ExternalServiceError(
            "llm provider rate limited", service=SERVICE, details={"status": 429}
        )
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        return ExternalServiceError(
            f"llm provider returned HTTP {status}: {exc.message[:200]}",
            service=SERVICE,
            details={"status": status},
            retryable=status >= 500,
        )
    if isinstance(exc, openai.APIConnectionError | openai.APITimeoutError):
        return ExternalServiceError(
            f"llm provider unreachable: {type(exc).__name__}", service=SERVICE
        )
    return ExternalServiceError(
        f"llm call failed: {type(exc).__name__}: {str(exc)[:200]}",
        service=SERVICE,
        retryable=False,
    )
