"""Where playbook runs live (``playbook_runs`` and ``playbook_steps``)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import NotFound

RunStatus = Literal["running", "waiting", "waiting_approval", "done", "failed", "cancelled"]
ACTIVE: frozenset[str] = frozenset({"running", "waiting", "waiting_approval"})


class StepRecord(StrictModel):
    step_id: str
    status: str  # done | skipped | waiting | failed | escalated
    at: datetime
    detail: dict[str, Any] = {}


class PlaybookRun(StrictModel):
    id: int
    playbook: str
    case_id: str
    po_name: str | None = None
    partner_id: int | None = None
    status: RunStatus = "running"
    step_index: int = 0
    due_at: datetime | None = None
    waiting_for: str | None = None
    started_by: str | None = None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    summary: str | None = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


@runtime_checkable
class PlaybookStore(Protocol):
    async def create(
        self,
        *,
        playbook: str,
        case_id: str,
        po_name: str | None,
        partner_id: int | None,
        started_by: str | None,
    ) -> PlaybookRun: ...

    async def get(self, run_id: int) -> PlaybookRun | None: ...

    async def update(self, run_id: int, **fields: Any) -> PlaybookRun: ...

    async def record_step(self, run_id: int, record: StepRecord) -> None: ...

    async def steps(self, run_id: int) -> list[StepRecord]: ...

    async def active_for_po(self, po_name: str) -> list[PlaybookRun]: ...

    async def active_for_case(self, case_id: str) -> PlaybookRun | None: ...

    async def active(self) -> list[PlaybookRun]: ...

    async def recent(self, *, limit: int = 100) -> list[PlaybookRun]: ...


def _js(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _run(row: dict[str, Any]) -> PlaybookRun:
    return PlaybookRun(
        id=int(row["id"]),
        playbook=str(row["playbook"]),
        case_id=str(row["case_id"]),
        po_name=row.get("po_name"),
        partner_id=row.get("partner_id"),
        status=str(row.get("status") or "running"),  # type: ignore[arg-type]
        step_index=int(row.get("step_index") or 0),
        due_at=row.get("due_at"),
        waiting_for=row.get("waiting_for"),
        started_by=row.get("started_by"),
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        finished_at=row.get("finished_at"),
        summary=row.get("summary"),
    )


class PostgresPlaybookStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        playbook: str,
        case_id: str,
        po_name: str | None,
        partner_id: int | None,
        started_by: str | None,
    ) -> PlaybookRun:
        row = await self._db.fetch_one(
            "INSERT INTO playbook_runs (playbook, case_id, po_name, partner_id, started_by) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING *",
            (playbook, case_id, po_name, partner_id, started_by),
        )
        assert row is not None
        return _run(row)

    async def get(self, run_id: int) -> PlaybookRun | None:
        row = await self._db.fetch_one("SELECT * FROM playbook_runs WHERE id = %s", (run_id,))
        return _run(row) if row else None

    async def update(self, run_id: int, **fields: Any) -> PlaybookRun:
        allowed = ("status", "step_index", "due_at", "waiting_for", "finished_at", "summary")
        sets = [f"{k} = %s" for k in fields if k in allowed]
        values = [fields[k] for k in fields if k in allowed]
        row = await self._db.fetch_one(
            f"UPDATE playbook_runs SET {', '.join([*sets, 'updated_at = now()'])} "
            "WHERE id = %s RETURNING *",
            (*values, run_id),
        )
        if row is None:
            raise NotFound(f"playbook run {run_id} not found")
        return _run(row)

    async def record_step(self, run_id: int, record: StepRecord) -> None:
        await self._db.execute(
            "INSERT INTO playbook_steps (run_id, step_id, status, at, detail) "
            "VALUES (%s, %s, %s, %s, %s::jsonb)",
            (
                run_id,
                record.step_id,
                record.status,
                record.at,
                json.dumps(record.detail, default=str),
            ),
        )

    async def steps(self, run_id: int) -> list[StepRecord]:
        rows = await self._db.fetch_all(
            "SELECT step_id, status, at, detail FROM playbook_steps WHERE run_id = %s ORDER BY id",
            (run_id,),
        )
        return [
            StepRecord(
                step_id=str(r["step_id"]),
                status=str(r["status"]),
                at=r["at"],
                detail=_js(r.get("detail")) or {},
            )
            for r in rows
        ]

    async def active_for_po(self, po_name: str) -> list[PlaybookRun]:
        rows = await self._db.fetch_all(
            "SELECT * FROM playbook_runs WHERE po_name = %s AND status = ANY(%s) ORDER BY id",
            (po_name, list(ACTIVE)),
        )
        return [_run(r) for r in rows]

    async def active_for_case(self, case_id: str) -> PlaybookRun | None:
        row = await self._db.fetch_one(
            "SELECT * FROM playbook_runs WHERE case_id = %s AND status = ANY(%s) "
            "ORDER BY id DESC LIMIT 1",
            (case_id, list(ACTIVE)),
        )
        return _run(row) if row else None

    async def active(self) -> list[PlaybookRun]:
        rows = await self._db.fetch_all(
            "SELECT * FROM playbook_runs WHERE status = ANY(%s) ORDER BY id", (list(ACTIVE),)
        )
        return [_run(r) for r in rows]

    async def recent(self, *, limit: int = 100) -> list[PlaybookRun]:
        rows = await self._db.fetch_all(
            "SELECT * FROM playbook_runs ORDER BY updated_at DESC LIMIT %s", (limit,)
        )
        return [_run(r) for r in rows]


class MemoryPlaybookStore:
    def __init__(self, now: Any = None) -> None:
        self.rows: dict[int, PlaybookRun] = {}
        self.step_rows: dict[int, list[StepRecord]] = {}
        self._now = now or (lambda: datetime.now(UTC))

    async def create(
        self,
        *,
        playbook: str,
        case_id: str,
        po_name: str | None,
        partner_id: int | None,
        started_by: str | None,
    ) -> PlaybookRun:
        run_id = max(self.rows, default=0) + 1
        now = self._now()
        run = PlaybookRun(
            id=run_id,
            playbook=playbook,
            case_id=case_id,
            po_name=po_name,
            partner_id=partner_id,
            started_by=started_by,
            started_at=now,
            updated_at=now,
        )
        self.rows[run_id] = run
        self.step_rows[run_id] = []
        return run

    async def get(self, run_id: int) -> PlaybookRun | None:
        return self.rows.get(run_id)

    async def update(self, run_id: int, **fields: Any) -> PlaybookRun:
        if run_id not in self.rows:
            raise NotFound(f"playbook run {run_id} not found")
        updated = self.rows[run_id].model_copy(update={**fields, "updated_at": self._now()})
        self.rows[run_id] = updated
        return updated

    async def record_step(self, run_id: int, record: StepRecord) -> None:
        self.step_rows.setdefault(run_id, []).append(record)

    async def steps(self, run_id: int) -> list[StepRecord]:
        return list(self.step_rows.get(run_id, []))

    async def active_for_po(self, po_name: str) -> list[PlaybookRun]:
        return [r for r in self.rows.values() if r.po_name == po_name and r.active]

    async def active_for_case(self, case_id: str) -> PlaybookRun | None:
        matches = [r for r in self.rows.values() if r.case_id == case_id and r.active]
        return matches[-1] if matches else None

    async def active(self) -> list[PlaybookRun]:
        return [r for r in self.rows.values() if r.active]

    async def recent(self, *, limit: int = 100) -> list[PlaybookRun]:
        return sorted(self.rows.values(), key=lambda r: r.updated_at, reverse=True)[:limit]
