"""Tests for sc_core.llm.registry and the LLM settings."""

from pathlib import Path

import pytest

from sc_core.infra.settings import Settings
from sc_core.llm.registry import Registry, expand_env, reset_registry_cache
from sc_core.shared.errors import ConfigurationError


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    reset_registry_cache()


def test_bundled_registry_is_consistent() -> None:
    reg = Registry.load()
    assert {"deepseek-v4-flash", "gpt-5.4", "gpt-5.3"} <= set(reg.models)
    for model in reg.models.values():
        assert model.provider in reg.providers
    assert reg.provider_for("deepseek-v4-flash").base_url == "https://api.deepseek.com/v1"


def test_unknown_model_and_provider() -> None:
    reg = Registry.load()
    with pytest.raises(ConfigurationError, match="unknown model"):
        reg.model("gpt-99")
    with pytest.raises(ConfigurationError, match="unknown provider"):
        reg.provider("nope")


def test_api_key_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = Registry.load().provider("deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert provider.configured is False
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        provider.api_key()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert provider.api_key() == "sk-test" and provider.configured


def test_env_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
    assert (
        expand_env("${LOCAL_LLM_BASE_URL:-http://localhost:11434/v1}")
        == "http://localhost:11434/v1"
    )
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://vllm:8000/v1")
    assert expand_env("x ${LOCAL_LLM_BASE_URL:-d} y") == "x http://vllm:8000/v1 y"
    assert expand_env("${MISSING}") == ""


def test_cost_split() -> None:
    spec = Registry.load().model("deepseek-v4-flash")
    cost = spec.cost(1_000_000, 500_000)
    assert cost == {"input": 0.14, "output": 0.14, "total": 0.28}


def test_custom_registry_file_validation(tmp_path: Path) -> None:
    bad = tmp_path / "models.yaml"
    bad.write_text(
        "providers: {}\nmodels:\n  m:\n    provider: ghost\n    context_window: 1\n"
        "    max_output_tokens: 1\n    price_per_mtok: {input: 0, output: 0}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="unknown provider 'ghost'"):
        Registry.load(bad)


def test_settings_model_assignment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SC__LLM__MODEL__SUPPLIER_COMMS", "gpt-5.4")
    s = Settings(_env_file=None)
    assert s.llm.model_for("supplier_comms") == "gpt-5.4"
    assert s.llm.model_for("director") == "deepseek-v4-flash"
    assert s.langfuse.configured is False
    clean_env.setenv("SC__LANGFUSE__PUBLIC_KEY", "pk")
    clean_env.setenv("SC__LANGFUSE__SECRET_KEY", "sk")
    assert Settings(_env_file=None).langfuse.configured is True


def test_settings_exports_provider_keys_from_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEEPSEEK_API_KEY in .env must reach os.environ, where the registry reads it."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-file\nSC__SERVICE_NAME=t\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.service_name == "t"
    assert Registry.load().provider("deepseek").api_key() == "sk-from-file"
