"""Run history (``scheduler_runs``), shown in the UI in phase 8."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.base import MutableModel

RunStatus = Literal["running", "ok", "failed", "skipped_overlap"]
Trigger = Literal["cron", "manual"]


class RunRecord(MutableModel):
    run_id: str
    job_id: str
    trigger: Trigger
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = "running"
    http_status: int | None = None
    summary: str | None = None


@runtime_checkable
class RunStore(Protocol):
    async def save(self, record: RunRecord) -> None: ...

    async def last(self, job_id: str) -> RunRecord | None: ...

    async def recent(self, limit: int = 50) -> list[RunRecord]: ...


class PostgresRunStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, record: RunRecord) -> None:
        await self._db.execute(
            "INSERT INTO scheduler_runs "
            "(run_id, job_id, trigger, started_at, finished_at, status, http_status, summary) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (run_id) DO UPDATE SET "
            "finished_at = EXCLUDED.finished_at, status = EXCLUDED.status, "
            "http_status = EXCLUDED.http_status, summary = EXCLUDED.summary",
            (
                record.run_id,
                record.job_id,
                record.trigger,
                record.started_at,
                record.finished_at,
                record.status,
                record.http_status,
                record.summary,
            ),
        )

    async def last(self, job_id: str) -> RunRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM scheduler_runs WHERE job_id = %s ORDER BY started_at DESC LIMIT 1",
            (job_id,),
        )
        return RunRecord.model_validate(row) if row else None

    async def recent(self, limit: int = 50) -> list[RunRecord]:
        rows = await self._db.fetch_all(
            "SELECT * FROM scheduler_runs ORDER BY started_at DESC LIMIT %s", (limit,)
        )
        return [RunRecord.model_validate(r) for r in rows]


class MemoryRunStore:
    def __init__(self) -> None:
        self.records: dict[str, RunRecord] = {}

    async def save(self, record: RunRecord) -> None:
        self.records[record.run_id] = record.model_copy()

    async def last(self, job_id: str) -> RunRecord | None:
        matching = [r for r in self.records.values() if r.job_id == job_id]
        return max(matching, key=lambda r: r.started_at) if matching else None

    async def recent(self, limit: int = 50) -> list[RunRecord]:
        ordered = sorted(self.records.values(), key=lambda r: r.started_at, reverse=True)
        return ordered[:limit]

    def statuses(self, job_id: str) -> list[Any]:
        return [r.status for r in self.records.values() if r.job_id == job_id]
