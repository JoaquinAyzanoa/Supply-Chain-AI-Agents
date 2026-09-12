"""The chat client follows the model chosen in the Control Tower."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sc_core.infra.runtime_settings import MemoryRuntimeSettingsReader
from sc_core.llm.client import ChatResult, MessageLike, Usage
from sc_core.llm.registry import ModelSpec
from sc_core.llm.switching import RuntimeModelClient
from sc_core.llm.testing import FAKE_SPEC
from sc_core.schema.runtime_settings import RuntimeSettings


class FakeClient:
    def __init__(self, model: str) -> None:
        self.spec = ModelSpec(**{**FAKE_SPEC.model_dump(), "name": model})
        self.calls = 0

    async def complete(self, messages: Sequence[MessageLike], **options: Any) -> ChatResult:
        self.calls += 1
        return ChatResult(
            text=self.spec.name,
            messages=[],
            finish_reason="stop",
            model=self.spec.name,
            usage=Usage(input_tokens=1, output_tokens=1),
            cost_usd=0.0,
            duration_ms=0.0,
        )


async def test_runtime_model_client_switches_and_caches_per_model() -> None:
    built: dict[str, FakeClient] = {}

    def build(model: str) -> FakeClient:
        built[model] = FakeClient(model)
        return built[model]

    reader = MemoryRuntimeSettingsReader(RuntimeSettings())
    client = RuntimeModelClient("director", build=build, default_model="base", runtime=reader)
    assert client.spec.name == "base" and await client.resolve_model() == "base"
    assert (await client.complete([])).text == "base"

    reader.set(RuntimeSettings(model_by_agent={"director": "better"}))
    assert (await client.complete([])).text == "better" and client.model_name == "better"
    assert client.spec.name == "better" and set(built) == {"base", "better"}

    reader.set(RuntimeSettings())
    assert (await client.complete([])).text == "base"
    assert built["base"].calls == 2 and built["better"].calls == 1  # reused, not rebuilt


async def test_without_a_reader_the_environment_model_is_used() -> None:
    client = RuntimeModelClient("x", build=FakeClient, default_model="env-model")
    assert (await client.complete([])).text == "env-model"
