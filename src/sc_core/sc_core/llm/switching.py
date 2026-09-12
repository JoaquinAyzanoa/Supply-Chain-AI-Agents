"""A chat client that follows the model chosen in the Control Tower.

``RuntimeModelClient`` looks up the agent's model in the runtime settings on
every call (cached a minute by the reader), builds one real client per model
name the first time it is needed and delegates to it. ``spec`` reflects the
client used last, so run logs record the model that actually answered.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from loguru import logger
from pydantic import BaseModel

from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.llm.client import ChatCompleter, ChatResult, MessageLike
from sc_core.llm.registry import ModelSpec


class RuntimeModelClient:
    def __init__(
        self,
        agent_name: str,
        *,
        build: Callable[[str], ChatCompleter],
        default_model: str,
        runtime: RuntimeSettingsReader | None = None,
    ) -> None:
        self.agent_name = agent_name
        self._build = build
        self._default_model = default_model
        self._runtime = runtime
        self._clients: dict[str, ChatCompleter] = {}
        self._current = default_model

    @property
    def spec(self) -> ModelSpec:
        return self._client_for(self._current).spec

    @property
    def model_name(self) -> str:
        """The model the last call used (the configured one before any call)."""
        return self._current

    async def resolve_model(self) -> str:
        """The model the next call will use: the runtime choice, else the environment's."""
        if self._runtime is None:
            return self._default_model
        chosen = (await self._runtime.current()).model_for(self.agent_name)
        return chosen or self._default_model

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
        model = await self.resolve_model()
        if model != self._current:
            logger.bind(agent=self.agent_name, model=model).info("model switched by settings")
            self._current = model
        return await self._client_for(model).complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            tools=tools,
            metadata=metadata,
            name=name,
        )

    def _client_for(self, model: str) -> ChatCompleter:
        if model not in self._clients:
            self._clients[model] = self._build(model)
        return self._clients[model]
