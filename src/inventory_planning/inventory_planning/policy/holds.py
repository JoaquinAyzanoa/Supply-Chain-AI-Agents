"""The planner's memory of rejected rule changes.

A reorder-rule change a person rejects twice within ``window_days`` is held:
the product's rule is not proposed again until ``held_until``. The line still
shows in the plan, marked as held, so nobody wonders why the planner went
quiet on it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.base import StrictModel

WINDOW_DAYS = 60
HOLD_DAYS = 30
REJECTIONS_TO_HOLD = 2


class PlanningHold(StrictModel):
    product_id: int
    rejections: int = 0
    first_rejected_on: date | None = None
    held_until: date | None = None
    last_approval_id: int | None = None


def next_hold(
    current: PlanningHold | None, product_id: int, *, approval_id: int | None, today: date
) -> PlanningHold:
    """One more rejection: count it inside the window, hold after the second."""
    if current is None or current.first_rejected_on is None:
        current = PlanningHold(product_id=product_id, rejections=0, first_rejected_on=today)
    elif (today - current.first_rejected_on).days > WINDOW_DAYS:
        current = PlanningHold(product_id=product_id, rejections=0, first_rejected_on=today)
    rejections = current.rejections + 1
    held_until = today + timedelta(days=HOLD_DAYS) if rejections >= REJECTIONS_TO_HOLD else None
    return PlanningHold(
        product_id=product_id,
        rejections=rejections,
        first_rejected_on=current.first_rejected_on,
        held_until=held_until,
        last_approval_id=approval_id,
    )


@runtime_checkable
class HoldStore(Protocol):
    async def held(self, product_ids: list[int], *, today: date) -> dict[int, date]:
        """Products whose rule changes are held, with the date the hold ends."""
        ...

    async def note_rejection(
        self, product_id: int, *, approval_id: int | None, today: date
    ) -> PlanningHold: ...


class MemoryHoldStore:
    def __init__(self) -> None:
        self.rows: dict[int, PlanningHold] = {}

    async def held(self, product_ids: list[int], *, today: date) -> dict[int, date]:
        return {
            pid: hold.held_until
            for pid, hold in self.rows.items()
            if pid in product_ids and hold.held_until is not None and hold.held_until >= today
        }

    async def note_rejection(
        self, product_id: int, *, approval_id: int | None, today: date
    ) -> PlanningHold:
        hold = next_hold(
            self.rows.get(product_id), product_id, approval_id=approval_id, today=today
        )
        self.rows[product_id] = hold
        return hold


class PostgresHoldStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def held(self, product_ids: list[int], *, today: date) -> dict[int, date]:
        if not product_ids:
            return {}
        rows = await self._db.fetch_all(
            "SELECT product_id, held_until FROM planning_holds "
            "WHERE product_id = ANY(%s) AND held_until >= %s",
            (list(product_ids), today),
        )
        return {int(r["product_id"]): r["held_until"] for r in rows}

    async def note_rejection(
        self, product_id: int, *, approval_id: int | None, today: date
    ) -> PlanningHold:
        row = await self._db.fetch_one(
            "SELECT * FROM planning_holds WHERE product_id = %s", (product_id,)
        )
        current = _row(row) if row else None
        hold = next_hold(current, product_id, approval_id=approval_id, today=today)
        await self._db.execute(
            "INSERT INTO planning_holds (product_id, rejections, first_rejected_on, held_until, "
            "last_approval_id) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (product_id) DO UPDATE SET rejections = EXCLUDED.rejections, "
            "first_rejected_on = EXCLUDED.first_rejected_on, held_until = EXCLUDED.held_until, "
            "last_approval_id = EXCLUDED.last_approval_id, updated_at = now()",
            (
                hold.product_id,
                hold.rejections,
                hold.first_rejected_on,
                hold.held_until,
                hold.last_approval_id,
            ),
        )
        return hold


def _row(row: dict[str, Any]) -> PlanningHold:
    return PlanningHold(
        product_id=int(row["product_id"]),
        rejections=int(row.get("rejections") or 0),
        first_rejected_on=row.get("first_rejected_on"),
        held_until=row.get("held_until"),
        last_approval_id=row.get("last_approval_id"),
    )
