"""LLM access for every agent.

Nothing outside this package imports ``openai`` or ``agent_framework``
clients directly. ``get_chat_client(agent_name)`` returns the traced client
for the model assigned to that agent (``SC__LLM__MODEL__<AGENT>`` or the
default); ``complete_structured`` validates JSON answers against a Pydantic
model; ``RunBudget`` caps spend per run.
"""

from __future__ import annotations

from pathlib import Path

from sc_core.infra.settings import Settings, get_settings
from sc_core.llm.budget import RunBudget, current_budget, get_budget
from sc_core.llm.client import (
    ChatCompleter,
    ChatResult,
    TracedChatClient,
    Usage,
    assistant,
    system,
    user,
)
from sc_core.llm.registry import ModelSpec, Registry
from sc_core.llm.structured import StructuredOutputFailed, complete_structured


def get_chat_client(
    agent_name: str, *, settings: Settings | None = None, model: str | None = None
) -> ChatCompleter:
    """The client for ``agent_name`` (``model`` overrides the configured one).

    Honours ``SC__LLM__RECORD_MODE`` for cassettes.
    """
    settings = settings or get_settings()
    cfg = settings.llm
    model_name = model or cfg.model_for(agent_name)
    if cfg.record_mode == "replay":
        from sc_core.llm.testing import ChatCassette, ReplayChatClient

        spec = Registry.load().model(model_name)
        return ReplayChatClient(ChatCassette.load(_cassette_path(agent_name)), spec)
    client: ChatCompleter = TracedChatClient.build(agent_name, cfg=cfg, model=model_name)
    if cfg.record_mode == "record":
        from sc_core.llm.testing import ChatCassette, RecordingChatClient

        path = _cassette_path(agent_name)
        cassette = ChatCassette.load(path) if path.exists() else ChatCassette(path=path)
        return RecordingChatClient(client, cassette)
    return client


def default_budget(settings: Settings | None = None) -> RunBudget:
    cfg = (settings or get_settings()).llm
    return RunBudget(
        max_input_tokens=cfg.budget_max_input_tokens,
        max_output_tokens=cfg.budget_max_output_tokens,
        max_usd=cfg.budget_max_usd,
    )


def _cassette_path(agent_name: str) -> Path:
    return Path("tests") / "fixtures" / "llm" / f"{agent_name}.json"


__all__ = [
    "ChatCompleter",
    "ChatResult",
    "ModelSpec",
    "Registry",
    "RunBudget",
    "StructuredOutputFailed",
    "TracedChatClient",
    "Usage",
    "assistant",
    "complete_structured",
    "current_budget",
    "default_budget",
    "get_budget",
    "get_chat_client",
    "system",
    "user",
]
