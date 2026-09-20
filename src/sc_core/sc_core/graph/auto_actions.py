"""The record of what ran without a person (``auto_actions``).

The approval gateway writes one row when the autonomy policy lets an action
through; the Control Tower shows the rows as the "done automatically" feed
and reverts the ones that carry an inverse write while their window is open.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol

from sc_core.infra.db import Database
from sc_core.schema.base import StrictModel


class AutoActionRecord(StrictModel):
    case_id: str | None = None
    run_id: str | None = None
    agent: str
    kind: str
    level: str
    rule_id: str | None = None
    summary: str
    po_id: int | None = None
    po_name: str | None = None
    partner_id: int | None = None
    payload: dict[str, Any] = {}
    revert: dict[str, Any] | None = None
    revert_until: datetime | None = None


class AutoActionPorts(Protocol):
    async def record(self, action: AutoActionRecord) -> int: ...


class PostgresAutoActions:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, action: AutoActionRecord) -> int:
        row = await self._db.fetch_one(
            "INSERT INTO auto_actions (case_id, run_id, agent, kind, level, rule_id, summary, "
            "po_id, po_name, partner_id, payload, revert, revert_until) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s) "
            "RETURNING id",
            (
                action.case_id,
                action.run_id,
                action.agent,
                action.kind,
                action.level,
                action.rule_id,
                action.summary[:500],
                action.po_id,
                action.po_name,
                action.partner_id,
                json.dumps(action.payload, default=str),
                json.dumps(action.revert, default=str) if action.revert is not None else None,
                action.revert_until,
            ),
        )
        return int(row["id"]) if row else 0


class MemoryAutoActions:
    """For tests: keeps the rows in a list."""

    def __init__(self) -> None:
        self.rows: list[AutoActionRecord] = []

    async def record(self, action: AutoActionRecord) -> int:
        self.rows.append(action)
        return len(self.rows)
