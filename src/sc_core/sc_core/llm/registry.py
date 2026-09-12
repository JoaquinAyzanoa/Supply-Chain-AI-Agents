"""Provider and model registry, loaded from ``models.yaml``.

The registry answers two questions for a model name: which provider serves
it (base URL and the environment variable holding the key) and what it can
do (``ModelSpec``). Keys are read from the environment on demand so the
registry itself never holds a secret.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sc_core.shared.errors import ConfigurationError

_ENV_REF = re.compile(r"\$\{(?P<name>[A-Z0-9_]+)(?::-(?P<default>[^}]*))?\}")
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("models.yaml")


def expand_env(value: str) -> str:
    """``${VAR:-default}`` expansion, as in a shell."""

    def replace(match: re.Match[str]) -> str:
        return os.environ.get(match["name"]) or (match["default"] or "")

    return _ENV_REF.sub(replace, value)


class Price(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input: float = Field(ge=0)
    output: float = Field(ge=0)


class ProviderSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    base_url: str
    api_key_env: str

    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise ConfigurationError(
                f"provider {self.name!r} needs the environment variable {self.api_key_env}"
            )
        return key

    @property
    def configured(self) -> bool:
        return bool(os.environ.get(self.api_key_env))


class ModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    provider: str
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    parallel_tool_calls: bool = True
    json_schema_output: bool = True
    reasoning: bool = False
    price_per_mtok: Price

    def cost(self, input_tokens: int, output_tokens: int) -> dict[str, float]:
        """USD cost split the way Langfuse expects (``input``, ``output``, ``total``)."""
        cost_in = input_tokens * self.price_per_mtok.input / 1_000_000
        cost_out = output_tokens * self.price_per_mtok.output / 1_000_000
        return {"input": cost_in, "output": cost_out, "total": cost_in + cost_out}


class Registry(BaseModel):
    model_config = ConfigDict(frozen=True)

    providers: dict[str, ProviderSpec]
    models: dict[str, ModelSpec]

    @classmethod
    def load(cls, path: Path | None = None) -> Registry:
        return _load(path or DEFAULT_REGISTRY_PATH)

    def model(self, name: str) -> ModelSpec:
        try:
            return self.models[name]
        except KeyError:
            raise ConfigurationError(
                f"unknown model {name!r}; known: {', '.join(sorted(self.models))}"
            ) from None

    def provider(self, name: str) -> ProviderSpec:
        try:
            return self.providers[name]
        except KeyError:
            raise ConfigurationError(f"unknown provider {name!r}") from None

    def provider_for(self, model_name: str) -> ProviderSpec:
        return self.provider(self.model(model_name).provider)


@lru_cache(maxsize=4)
def _load(path: Path) -> Registry:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    providers = {
        name: ProviderSpec(name=name, **{**spec, "base_url": expand_env(str(spec["base_url"]))})
        for name, spec in (raw.get("providers") or {}).items()
    }
    models = {
        name: ModelSpec(name=name, **spec) for name, spec in (raw.get("models") or {}).items()
    }
    for model in models.values():
        if model.provider not in providers:
            raise ConfigurationError(
                f"model {model.name!r} references unknown provider {model.provider!r}"
            )
    return Registry(providers=providers, models=models)


def reset_registry_cache() -> None:
    _load.cache_clear()
