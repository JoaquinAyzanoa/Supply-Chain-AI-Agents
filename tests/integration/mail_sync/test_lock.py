"""RedisLock against a real Redis."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from redis import asyncio as redis_async

from sc_core.infra.locks import RedisLock

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(redis_dsn: str) -> AsyncIterator[redis_async.Redis]:
    r = redis_async.from_url(redis_dsn)
    await r.flushdb()
    yield r
    await r.aclose()


async def test_second_holder_is_refused_until_release(client: redis_async.Redis) -> None:
    lock = RedisLock(client)
    async with lock.acquire("mail_sync", ttl_seconds=30) as held:
        assert held is True
        async with lock.acquire("mail_sync", ttl_seconds=30) as again:
            assert again is False
    async with lock.acquire("mail_sync", ttl_seconds=30) as after:
        assert after is True


async def test_release_only_by_owner_and_ttl_expiry(client: redis_async.Redis) -> None:
    lock = RedisLock(client)
    token = await lock.try_acquire("job", ttl_seconds=1)
    assert token is not None
    assert await lock.release("job", "not-the-token") is False
    assert await client.exists("sc:lock:job") == 1
    await asyncio.sleep(1.2)  # TTL frees a lock whose holder died
    assert await lock.try_acquire("job", ttl_seconds=1) is not None


async def test_two_concurrent_runs_one_wins(client: redis_async.Redis) -> None:
    lock = RedisLock(client)
    outcomes: list[bool] = []

    async def run() -> None:
        async with lock.acquire("mail_sync", ttl_seconds=5) as held:
            outcomes.append(held)
            if held:
                await asyncio.sleep(0.2)

    await asyncio.gather(run(), run())
    assert sorted(outcomes) == [False, True]
