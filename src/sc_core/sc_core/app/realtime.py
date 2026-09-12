"""Live updates for the Control Tower.

The director publishes small notifications (``case_updated``,
``approval_created``, ``approval_resolved``, ``run_finished``,
``settings_changed``) and the UI listens on ``GET /api/stream`` (Server-Sent
Events). The bridge is Redis pub/sub so every director replica serves the
same stream. Payloads carry identifiers only; the UI refetches what it
shows. ``MemoryRealtime`` backs tests and single-process harnesses.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from sc_core.schema.base import StrictModel
from sc_core.shared.time import utc_now

CHANNEL = "sc:realtime"


class RealtimeEvent(StrictModel):
    kind: str
    payload: dict[str, Any] = {}
    at: datetime

    def sse(self) -> str:
        """One SSE frame: ``event: <kind>`` and a JSON ``data`` line."""
        data = json.dumps({"payload": self.payload, "at": self.at.isoformat()}, default=str)
        return f"event: {self.kind}\ndata: {data}\n\n"


@runtime_checkable
class Realtime(Protocol):
    async def publish(self, kind: str, payload: dict[str, Any] | None = None) -> None: ...

    def subscribe(self) -> AsyncGenerator[RealtimeEvent]:
        """Events published after the subscription started, in order."""
        ...


class NoRealtime:
    async def publish(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        return None

    async def subscribe(self) -> AsyncGenerator[RealtimeEvent]:
        return
        yield  # pragma: no cover - makes this an async generator


class MemoryRealtime:
    """In-process fan-out: every subscriber gets every event published after it joined."""

    def __init__(self) -> None:
        self.published: list[RealtimeEvent] = []
        self._queues: list[asyncio.Queue[RealtimeEvent]] = []

    async def publish(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        event = RealtimeEvent(kind=kind, payload=payload or {}, at=utc_now())
        self.published.append(event)
        for queue in list(self._queues):
            queue.put_nowait(event)

    async def subscribe(self) -> AsyncGenerator[RealtimeEvent]:
        queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue()
        self._queues.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues.remove(queue)


class RedisRealtime:
    """Redis pub/sub on one channel; the payload is the event as JSON."""

    def __init__(self, redis: Any, *, channel: str = CHANNEL) -> None:
        self._redis = redis
        self._channel = channel

    async def publish(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        event = RealtimeEvent(kind=kind, payload=payload or {}, at=utc_now())
        try:
            await self._redis.publish(self._channel, event.model_dump_json())
        except Exception as exc:  # a lost notification is not a lost update: the UI refetches
            logger.warning("realtime publish failed: {}", exc)

    async def subscribe(self) -> AsyncGenerator[RealtimeEvent]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    await asyncio.sleep(0)  # let cancellation through between polls
                    continue
                raw = message["data"]
                text = raw.decode() if isinstance(raw, bytes) else str(raw)
                try:
                    yield RealtimeEvent.model_validate_json(text)
                except ValueError:
                    logger.warning("ignoring a malformed realtime message")
        finally:
            await pubsub.unsubscribe(self._channel)
            await pubsub.aclose()


async def sse_frames(
    realtime: Realtime, *, heartbeat_seconds: float = 15.0, max_events: int | None = None
) -> AsyncIterator[str]:
    """SSE frames for a subscriber: events as they come, a comment line when idle.

    The heartbeat keeps proxies from closing an idle connection and lets the
    browser notice a dead one. ``max_events`` bounds the stream in tests.
    """
    # A pump task drains the subscription into a queue: waiting on the queue with a
    # timeout gives the heartbeat without ever cancelling the subscription itself
    # (cancelling ``anext`` on an async generator closes it).
    queue: asyncio.Queue[RealtimeEvent | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in realtime.subscribe():
                await queue.put(event)
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    await asyncio.sleep(0)  # let the subscription register before the client sees "connected"
    yield ": connected\n\n"
    sent = 0
    try:
        while max_events is None or sent < max_events:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is None:
                return
            yield item.sse()
            sent += 1
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
