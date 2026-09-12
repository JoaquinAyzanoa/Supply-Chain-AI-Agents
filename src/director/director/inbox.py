"""Where accepted events land, and where the orchestrator leaves its outcome.

The primary key on ``event_id`` makes redelivery idempotent: a producer that
retries after a timeout, or replays its outbox, never creates a second row.
``EventResults.record`` marks the row handled with a small JSON outcome
(case id, statuses). ``EventResults.defer`` leaves the row unhandled with
a reason (the order was locked); ``EventInbox.unhandled`` lists what the
daily job must replay, skipping rows a running task is still working on.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.events import BaseEvent, parse_event


@runtime_checkable
class EventInbox(Protocol):
    async def store(self, event: BaseEvent) -> bool:
        """Persist the event; ``False`` when it was already there."""
        ...

    async def unhandled(self, *, min_age_seconds: int = 60, limit: int = 100) -> list[BaseEvent]:
        """Events never handled (or deferred), oldest first; fresh rows are left to their task."""
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

    async def unhandled(self, *, min_age_seconds: int = 60, limit: int = 100) -> list[BaseEvent]:
        rows = await self._db.fetch_all(
            "SELECT payload FROM event_inbox WHERE handled_at IS NULL "
            "AND received_at < now() - make_interval(secs => %s) "
            "AND (result IS NULL OR result->>'status' = 'deferred' "
            "     OR received_at < now() - interval '1 hour') "
            "ORDER BY received_at LIMIT %s",
            (min_age_seconds, limit),
        )
        return [parse_event(row["payload"]) for row in rows]


class MemoryEventInbox:
    def __init__(self) -> None:
        self.events: dict[str, BaseEvent] = {}
        self.handled: set[str] = set()
        self.deferred: dict[str, str] = {}

    async def store(self, event: BaseEvent) -> bool:
        if event.event_id in self.events:
            return False
        self.events[event.event_id] = event
        return True

    async def unhandled(self, *, min_age_seconds: int = 60, limit: int = 100) -> list[BaseEvent]:
        return [e for k, e in self.events.items() if k not in self.handled][:limit]

    def of_type(self, event_type: str) -> list[Any]:
        return [e for e in self.events.values() if e.type == event_type]


@runtime_checkable
class EventResults(Protocol):
    async def record(self, event_id: str, result: dict[str, Any]) -> None: ...

    async def defer(self, event_id: str, reason: str) -> None:
        """Leave the event unhandled, with the reason, for the daily replay."""
        ...


class PostgresEventResults:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, event_id: str, result: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE event_inbox SET handled_at = now(), result = %s::jsonb WHERE event_id = %s",
            (json.dumps(result, default=str), event_id),
        )

    async def defer(self, event_id: str, reason: str) -> None:
        await self._db.execute(
            "UPDATE event_inbox SET result = %s::jsonb WHERE event_id = %s AND handled_at IS NULL",
            (json.dumps({"status": "deferred", "reason": reason}), event_id),
        )


class MemoryEventResults:
    def __init__(self, inbox: MemoryEventInbox | None = None) -> None:
        self.results: dict[str, dict[str, Any]] = {}
        self._inbox = inbox

    async def record(self, event_id: str, result: dict[str, Any]) -> None:
        self.results[event_id] = result
        if self._inbox is not None:
            self._inbox.handled.add(event_id)
            self._inbox.deferred.pop(event_id, None)

    async def defer(self, event_id: str, reason: str) -> None:
        if self._inbox is not None:
            self._inbox.deferred[event_id] = reason
