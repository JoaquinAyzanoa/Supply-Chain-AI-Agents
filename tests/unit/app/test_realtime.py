"""Realtime fan-out and the SSE frames the Control Tower reads."""

from __future__ import annotations

import asyncio

from sc_core.app.realtime import MemoryRealtime, NoRealtime, RealtimeEvent, sse_frames
from sc_core.shared.time import utc_now


def test_event_renders_as_an_sse_frame() -> None:
    event = RealtimeEvent(kind="case_updated", payload={"case_id": "c1"}, at=utc_now())
    frame = event.sse()
    assert frame.startswith("event: case_updated\ndata: {") and frame.endswith("\n\n")
    assert '"case_id": "c1"' in frame


async def test_memory_realtime_delivers_to_every_subscriber_after_it_joined() -> None:
    realtime = MemoryRealtime()
    await realtime.publish("early", {})
    received: list[str] = []

    async def listen() -> None:
        async for event in realtime.subscribe():
            received.append(event.kind)
            if len(received) == 2:
                return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.01)
    await realtime.publish("approval_created", {"approval_id": 1})
    await realtime.publish("run_finished", {"run_id": "r1"})
    await asyncio.wait_for(task, 1)
    assert received == ["approval_created", "run_finished"]
    assert [e.kind for e in realtime.published] == ["early", "approval_created", "run_finished"]


async def test_sse_frames_heartbeat_then_events_within_a_second() -> None:
    realtime = MemoryRealtime()
    frames: list[str] = []

    async def collect() -> None:
        async for frame in sse_frames(realtime, heartbeat_seconds=0.02, max_events=2):
            frames.append(frame)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.06)  # idle: heartbeats only
    started = asyncio.get_running_loop().time()
    await realtime.publish("case_updated", {"case_id": "c1"})
    await realtime.publish("approval_resolved", {"approval_id": 5})
    await asyncio.wait_for(task, 1)
    assert asyncio.get_running_loop().time() - started < 1.0
    assert frames[0] == ": connected\n\n" and ": ping\n\n" in frames
    events = [f for f in frames if f.startswith("event:")]
    assert [f.split("\n")[0] for f in events] == ["event: case_updated", "event: approval_resolved"]


async def test_no_realtime_stream_ends_immediately() -> None:
    frames = [f async for f in sse_frames(NoRealtime(), heartbeat_seconds=0.01)]
    assert frames == [": connected\n\n"]
