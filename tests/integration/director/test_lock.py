"""Per-order lock on a real Redis: two events for one order never run at once."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis import asyncio as redis_async

from director.concurrency import PoLocks
from director.inbox import MemoryEventInbox, MemoryEventResults
from director.testing import memory_deps
from director.workflow import Orchestrator
from sc_core.a2a import AgentReply
from sc_core.infra.locks import RedisLock
from tests.unit.director.helpers import agent_reply, linked

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(redis_dsn: str) -> AsyncIterator[redis_async.Redis]:
    r = redis_async.from_url(redis_dsn)
    await r.flushdb()
    yield r
    await r.aclose()


class SlowCaller:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def send(self, task_json: str, *, case_id: str, metadata: Any = None) -> AgentReply:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.2)
        self.active -= 1
        return agent_reply("handle_inbound", json.loads(task_json)["case_id"], "no_action", "ok")


async def test_same_order_is_serialised_across_orchestrators(client: redis_async.Redis) -> None:
    """Two director processes (two orchestrators on one Redis) still run one at a time per order."""
    caller = SlowCaller()
    inbox = MemoryEventInbox()

    def orchestrator() -> Orchestrator:
        locks = PoLocks(RedisLock(client), ttl_seconds=30, wait_seconds=5, poll_seconds=0.05)
        return Orchestrator(
            memory_deps(supplier_comms=caller), MemoryEventResults(inbox), inbox=inbox, locks=locks
        )

    first, second = orchestrator(), orchestrator()
    a = linked(case_id="case_a", graph_message_id="A", po_name="P00015")
    b = linked(case_id="case_b", graph_message_id="B", po_name="P00015")
    c = linked(case_id="case_c", graph_message_id="C", po_name="P00016")
    results = await asyncio.gather(first.handle(a), second.handle(b), second.handle(c))
    assert all(r.get("status") != "deferred" for r in results)
    assert caller.peak == 2  # P00015 twice in sequence, P00016 alongside
    assert await client.exists("sc:lock:po:P00015") == 0  # released


async def test_wait_exhausted_defers(client: redis_async.Redis) -> None:
    caller = SlowCaller()
    inbox = MemoryEventInbox()
    locks = PoLocks(RedisLock(client), ttl_seconds=30, wait_seconds=0.3, poll_seconds=0.05)
    orch = Orchestrator(
        memory_deps(supplier_comms=caller), MemoryEventResults(inbox), inbox=inbox, locks=locks
    )
    event = linked(case_id="case_a", graph_message_id="A", po_name="P00015")
    await inbox.store(event)
    token = await RedisLock(client).try_acquire("po:P00015", ttl_seconds=30)
    assert token is not None
    result = await orch.handle(event)
    assert result["status"] == "deferred" and caller.peak == 0
    await RedisLock(client).release("po:P00015", token)
    assert (await orch.replay_unhandled())["replayed"] == 1 and caller.peak == 1
