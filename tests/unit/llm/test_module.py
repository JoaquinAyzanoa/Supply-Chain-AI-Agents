"""LlmModule and get_chat_client wiring."""

from __future__ import annotations

import pytest
from injector import Injector

from sc_core.infra.health import HealthRegistry
from sc_core.infra.module import ChatClientFactory, CoreModule, LlmModule
from sc_core.infra.settings import Settings
from sc_core.llm import default_budget, get_chat_client
from sc_core.llm.client import TracedChatClient
from sc_core.llm.registry import Registry
from sc_core.shared.errors import ConfigurationError


def _settings(**llm: object) -> Settings:
    return Settings(_env_file=None, service_name="t", environment="test", llm=llm)  # type: ignore[arg-type]


def test_get_chat_client_builds_traced_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    client = get_chat_client("director", settings=_settings())
    assert isinstance(client, TracedChatClient)
    assert client.spec.name == "deepseek-v4-flash" and client.provider_name == "deepseek"


def test_get_chat_client_without_key_is_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        get_chat_client("director", settings=_settings())


def test_module_registers_health_and_caches_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    settings = _settings()
    health = HealthRegistry()
    injector = Injector([CoreModule(settings, health), LlmModule()])
    factory = injector.get(ChatClientFactory)
    assert injector.get(Registry).model("gpt-5.4").provider == "openai"
    assert "llm_provider" in health.names() and "langfuse" not in health.names()
    assert factory.for_agent("director") is factory.for_agent("director")


def test_default_budget_from_settings() -> None:
    budget = default_budget(_settings(budget_max_usd=0.5))
    assert budget.max_usd == 0.5 and budget.max_input_tokens == 400_000
