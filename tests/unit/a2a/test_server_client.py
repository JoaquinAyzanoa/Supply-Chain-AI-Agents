"""A2A round trip in-process: mount an echo agent, call it, check auth and trace metadata."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
import pytest
from loguru import logger

from sc_core.a2a import AgentReply, AgentSpec, Skill, mount
from sc_core.a2a.client import A2AClient
from sc_core.a2a.testing import FakeAgentCaller, in_process_client
from sc_core.app import create_application
from sc_core.infra import context
from sc_core.infra.settings import Settings
from sc_core.shared.errors import ExternalServiceError, NotFound

TOKEN = "agent-token"


class Echo:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any], str | None]] = []

    async def handle(self, task_json: str, metadata: Mapping[str, Any]) -> AgentReply:
        task = json.loads(task_json)
        self.seen.append((task_json, dict(metadata), context.case_id.get()))
        if task.get("kind") == "boom":
            raise NotFound("no such order")
        if task.get("kind") == "pause":
            return AgentReply(status="input_required", text='{"status": "awaiting_approval"}')
        return AgentReply(status="completed", text=json.dumps({"echo": task}))


@pytest.fixture
def echo() -> Echo:
    return Echo()


@pytest.fixture
async def client(echo: Echo) -> AsyncIterator[A2AClient]:
    settings = Settings(_env_file=None, service_name="echo-agent", environment="test")
    app = create_application(settings)
    spec = AgentSpec(
        name="echo",
        description="echoes tasks",
        version="0.1.0",
        url="http://agent.test/a2a",
        skills=[Skill(id="echo", name="Echo", description="returns the task", tags=["test"])],
    )
    mount(app, echo, spec, token=TOKEN)
    a2a = in_process_client(app, token=TOKEN)
    yield a2a
    await a2a.aclose()
    logger.remove()


async def test_completed_round_trip_carries_case_and_trace(client: A2AClient, echo: Echo) -> None:
    reply = await client.send('{"kind": "send_rfq", "po_name": "P00015"}', case_id="case_1")
    assert reply.status == "completed" and reply.task_id
    assert json.loads(reply.text) == {"echo": {"kind": "send_rfq", "po_name": "P00015"}}
    task_json, metadata, bound_case = echo.seen[0]
    assert metadata["case_id"] == "case_1" and bound_case == "case_1"


async def test_input_required_and_failed_statuses(client: A2AClient) -> None:
    paused = await client.send('{"kind": "pause"}', case_id="case_2")
    assert paused.status == "input_required" and "awaiting_approval" in paused.text
    failed = await client.send('{"kind": "boom"}', case_id="case_3")
    assert failed.status == "failed" and json.loads(failed.text)["code"] == "not_found"


async def test_card_is_public_and_rpc_needs_bearer(echo: Echo) -> None:
    settings = Settings(_env_file=None, service_name="echo-agent", environment="test")
    app = create_application(settings)
    spec = AgentSpec(
        name="echo", description="d", version="1", url="http://agent.test/a2a", skills=[]
    )
    mount(app, echo, spec, token=TOKEN)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://agent.test"
    ) as http:
        card = await http.get("/.well-known/agent-card.json")
        assert card.status_code == 200 and card.json()["name"] == "echo"
        assert (await http.post("/a2a", json={})).status_code == 401
        wrong = await http.post("/a2a", json={}, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
    bad = in_process_client(app, token="nope")
    with pytest.raises(ExternalServiceError):
        await bad.send('{"kind": "x"}', case_id="c")
    await bad.aclose()
    logger.remove()


async def test_fake_caller_records_and_scripts() -> None:
    fake = FakeAgentCaller([AgentReply(status="completed", text="{}"), NotFound("x")])
    reply = await fake.send('{"kind": "a"}', case_id="c1")
    assert reply.status == "completed" and fake.sent[0].case_id == "c1"
    with pytest.raises(NotFound):
        await fake.send('{"kind": "b"}', case_id="c2")
    with pytest.raises(AssertionError):
        await fake.send('{"kind": "c"}', case_id="c3")
