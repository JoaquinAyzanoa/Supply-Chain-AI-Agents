"""Where accepted events land, and where the orchestrator leaves its outcome.

The primary key on ``event_id`` makes redelivery idempotent: a producer that
retries after a timeout, or replays its outbox, never creates a second row.
``EventResults.record`` marks the row handled with a small JSON outcome
(case id, statuses); rows still unhandled are replayed by the daily job.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.events import BaseEvent


@runtime_checkable
class EventInbox(Protocol):
    async def store(self, event: BaseEvent) -> bool:
        """Persist the event; ``False`` when it was already there."""
        ...


class PostgresEventInbox:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def store(self, event: BaseEvent) -> bool:
        inserted = await self._db.execute(
            "INSERT INTO event_inbox (event_id, event_type, source, case_id, payload) "
            "VALUES (%s, %s, %s, %s, %s::jsonb) ON CONFLICT (event_id) DO NOTHING",
            (event.event_id, event.type, event.source, event.case_id, event.model_dump_json()),
        )
        return inserted == 1


class MemoryEventInbox:
    def __init__(self) -> None:
        self.events: dict[str, BaseEvent] = {}

    async def store(self, event: BaseEvent) -> bool:
        if event.event_id in self.events:
            return False
        self.events[event.event_id] = event
        return True

    def of_type(self, event_type: str) -> list[Any]:
        return [e for e in self.events.values() if e.type == event_type]


@runtime_checkable
class EventResults(Protocol):
    async def record(self, event_id: str, result: dict[str, Any]) -> None: ...


class PostgresEventResults:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, event_id: str, result: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE event_inbox SET handled_at = now(), result = %s::jsonb WHERE event_id = %s",
            (json.dumps(result, default=str), event_id),
        )


class MemoryEventResults:
    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}

    async def record(self, event_id: str, result: dict[str, Any]) -> None:
        self.results[event_id] = result
