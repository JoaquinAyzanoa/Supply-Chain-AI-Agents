"""Traces reach the compose Langfuse with complete input and output."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest

from sc_core.infra import tracing
from sc_core.infra.settings import Settings
from sc_core.llm.client import TracedChatClient, system, user
from sc_core.llm.testing import FAKE_SPEC
from sc_core.prompts import get_prompt

pytestmark = [pytest.mark.integration]


class _Inner:
    """Stands in for the framework client so the test needs no provider key."""

    async def get_response(self, messages, *, options):  # type: ignore[no-untyped-def]
        from agent_framework import ChatResponse, Message, UsageDetails

        return ChatResponse(
            messages=[Message("assistant", ["la orden llega el 2026-10-20"])],
            usage_details=UsageDetails(input_token_count=21, output_token_count=9),
            model="fake-model",
            finish_reason="stop",
        )


def _api(settings: Settings) -> httpx.Client:
    return httpx.Client(
        base_url=settings.langfuse.host,
        auth=(settings.langfuse.public_key, settings.langfuse.secret_key.get_secret_value()),
        timeout=15,
    )


async def test_generation_and_spans_are_ingested(langfuse_settings: Settings) -> None:
    tracing.configure_tracing(langfuse_settings)
    marker = uuid4().hex[:8]
    client = TracedChatClient(_Inner(), spec=FAKE_SPEC, agent_name="itest", provider_name="fake")  # type: ignore[arg-type]

    with tracing.start_case(f"case_{marker}", f"trace-{marker}", input={"marker": marker}) as root:
        with tracing.tool_span("get_po", input={"po": "P00015"}) as tool:
            tool.update(output={"lines": 2})
        result = await client.complete(
            [system(f"system {marker}"), user("¿cuándo llega?")],
            temperature=0.1,
            name=f"gen-{marker}",
            metadata={"marker": marker},
        )
        root.update(output={"text": result.text})
    tracing.flush()

    # Ingestion is asynchronous; poll the public API.
    with _api(langfuse_settings) as api:
        for _ in range(30):
            observations = api.get(
                "/api/public/observations", params={"name": f"gen-{marker}"}
            ).json()
            if observations.get("data"):
                break
            await asyncio.sleep(2)
        else:
            pytest.fail("generation not visible in Langfuse after 60s")
        gen = observations["data"][0]
        assert gen["type"] == "GENERATION" and gen["model"] == "fake-model"
        assert gen["input"]["messages"][0]["contents"][0]["text"] == f"system {marker}"
        assert gen["output"]["messages"][0]["contents"][0]["text"].startswith("la orden")
        assert gen["usage"]["input"] == 21 and gen["usage"]["output"] == 9
        assert gen["metadata"]["provider"] == "fake" and gen["metadata"]["marker"] == marker
        assert gen["calculatedTotalCost"] or gen.get("totalCost") or gen["usage"]["total"] == 30

        tools = api.get(
            "/api/public/observations", params={"name": "tool.get_po", "traceId": gen["traceId"]}
        ).json()
        assert tools["data"] and tools["data"][0]["output"] == {"lines": 2}
        trace = api.get(f"/api/public/traces/{gen['traceId']}").json()
        assert trace["sessionId"] == f"case_{marker}" and trace["name"] == f"trace-{marker}"


def test_prompt_falls_back_when_not_in_langfuse(langfuse_settings: Settings) -> None:
    tracing.configure_tracing(langfuse_settings)
    prompt = get_prompt("supplier_tone", cfg=langfuse_settings.langfuse)
    assert prompt.text and prompt.source in ("local", "langfuse")
