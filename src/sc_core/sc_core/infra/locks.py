"""Distributed locks on Redis.

``RedisLock.try_acquire(name, ttl)`` takes the lock when nobody holds it and
returns ``None`` otherwise, so a job that finds the lock taken can report
"skipped" instead of waiting. The TTL guarantees release if the holder dies.
Release compares the token so a slow holder never deletes a successor's lock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import uuid4

from redis import asyncio as redis_async

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class Lock(Protocol):
    def acquire(self, name: str, *, ttl_seconds: int) -> AsyncIterator[bool]: ...


class RedisLock:
    def __init__(self, client: redis_async.Redis, *, prefix: str = "sc:lock:") -> None:
        self._r = client
        self._prefix = prefix

    async def try_acquire(self, name: str, *, ttl_seconds: int) -> str | None:
        token = uuid4().hex
        ok = await self._r.set(self._prefix + name, token, nx=True, px=ttl_seconds * 1000)
        return token if ok else None

    async def release(self, name: str, token: str) -> bool:
        result = await self._r.eval(_RELEASE, 1, self._prefix + name, token)
        return bool(result)

    @asynccontextmanager
    async def acquire(self, name: str, *, ttl_seconds: int) -> AsyncIterator[bool]:
        """Yield ``True`` while holding the lock, ``False`` (without waiting) when taken."""
        token = await self.try_acquire(name, ttl_seconds=ttl_seconds)
        if token is None:
            yield False
            return
        try:
            yield True
        finally:
            await self.release(name, token)


class MemoryLock:
    """Same contract for unit tests; ``held`` can be pre-set to simulate a running job."""

    def __init__(self) -> None:
        self.held: set[str] = set()
        self.acquired: list[str] = []

    @asynccontextmanager
    async def acquire(self, name: str, *, ttl_seconds: int) -> AsyncIterator[bool]:
        if name in self.held:
            yield False
            return
        self.held.add(name)
        self.acquired.append(name)
        try:
            yield True
        finally:
            self.held.discard(name)
