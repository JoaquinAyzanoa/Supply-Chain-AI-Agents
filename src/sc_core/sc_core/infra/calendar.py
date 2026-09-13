"""The demand calendar in the app database (migration 014), read by the planner and
edited from the Control Tower."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.calendar import CalendarEvent
from sc_core.shared.errors import NotFound


@runtime_checkable
class CalendarStore(Protocol):
    async def events(self, *, since: date | None = None) -> list[CalendarEvent]: ...

    async def add(self, event: CalendarEvent) -> CalendarEvent: ...

    async def remove(self, event_id: int) -> None: ...


def _event(row: dict[str, Any]) -> CalendarEvent:
    return CalendarEvent(
        id=int(row["id"]),
        kind=str(row["kind"]),  # type: ignore[arg-type]
        name=str(row["name"]),
        start_date=row["start_date"],
        end_date=row["end_date"],
        product_id=row.get("product_id"),
        category=row.get("category"),
        factor=float(row.get("factor") or 1.0),
        quantity=float(row.get("quantity") or 0.0),
        note=str(row.get("note") or ""),
        created_by=row.get("created_by"),
        created_at=row.get("created_at"),
    )


class PostgresCalendarStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def events(self, *, since: date | None = None) -> list[CalendarEvent]:
        if since is None:
            rows = await self._db.fetch_all("SELECT * FROM demand_calendar ORDER BY start_date, id")
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM demand_calendar WHERE end_date >= %s ORDER BY start_date, id",
                (since,),
            )
        return [_event(r) for r in rows]

    async def add(self, event: CalendarEvent) -> CalendarEvent:
        row = await self._db.fetch_one(
            "INSERT INTO demand_calendar (kind, name, start_date, end_date, product_id, category, "
            "factor, quantity, note, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING *",
            (
                event.kind,
                event.name,
                event.start_date,
                event.end_date,
                event.product_id,
                event.category,
                event.factor,
                event.quantity,
                event.note,
                event.created_by,
            ),
        )
        assert row is not None
        return _event(row)

    async def remove(self, event_id: int) -> None:
        deleted = await self._db.execute("DELETE FROM demand_calendar WHERE id = %s", (event_id,))
        if not deleted:
            raise NotFound(f"calendar event {event_id} not found")


class MemoryCalendarStore:
    def __init__(self) -> None:
        self.rows: dict[int, CalendarEvent] = {}

    async def events(self, *, since: date | None = None) -> list[CalendarEvent]:
        out = [e for e in self.rows.values() if since is None or e.end_date >= since]
        return sorted(out, key=lambda e: (e.start_date, e.id or 0))

    async def add(self, event: CalendarEvent) -> CalendarEvent:
        new_id = max(self.rows, default=0) + 1
        saved = event.model_copy(update={"id": new_id})
        self.rows[new_id] = saved
        return saved

    async def remove(self, event_id: int) -> None:
        if event_id not in self.rows:
            raise NotFound(f"calendar event {event_id} not found")
        del self.rows[event_id]


__all__ = ["CalendarStore", "MemoryCalendarStore", "PostgresCalendarStore"]
