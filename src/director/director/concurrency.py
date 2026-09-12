"""One run per purchase order at a time.

``PoLocks.hold(po_name)`` takes ``po:<name>`` on Redis for the duration of
a workflow run, waiting up to ``wait_seconds`` for a run on the same order
to finish (a reply that arrives while the ETA request is being drafted must
not race it). An event that cannot get the lock in time stays unhandled in
the inbox and the daily job replays it. Events without an order (unlinked
mail, scheduler ticks) never lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sc_core.infra.locks import Lock


class PoLocks:
    def __init__(
        self,
        lock: Lock | None,
        *,
        ttl_seconds: int = 900,
        wait_seconds: float = 120.0,
        poll_seconds: float = 0.5,
    ) -> None:
        self._lock = lock
        self._ttl = ttl_seconds
        self._wait = wait_seconds
        self._poll = poll_seconds

    @staticmethod
    def name_for(po_name: str) -> str:
        return f"po:{po_name}"

    @asynccontextmanager
    async def hold(self, po_name: str | None) -> AsyncIterator[bool]:
        """Yield ``True`` while holding the order's lock; ``False`` when the wait ran out."""
        if self._lock is None or not po_name:
            yield True
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._wait
        while True:
            async with self._lock.acquire(self.name_for(po_name), ttl_seconds=self._ttl) as held:
                if held:
                    yield True
                    return
            if loop.time() >= deadline:
                yield False
                return
            await asyncio.sleep(self._poll)
