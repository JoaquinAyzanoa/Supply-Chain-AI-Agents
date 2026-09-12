"""Cases: the orchestrator's unit of work (migration 004).

A case is one purchase order story: the RFQ and the replies it gets, an ETA
request and the confirmation, an inbound question. Every event that starts
work either attaches to an open case or opens a new one, so the Control
Tower and the Langfuse session show the whole story together.

Attach rule (``matches``): an open case on the same purchase order attaches
when the Outlook conversation is the same, or when either side has no
conversation yet (a PO confirmed by Odoo has none until the agent sends its
first email; a scheduler decision has none either). Terminal cases (done,
rejected, failed) never receive new work: the next event opens a new case.

Each task sent to an agent runs on its own thread (the event's ``case_id``
from phase 4), recorded in ``case_events``; the orchestrator's case id is
the grouping and the Langfuse session. Keeping the threads apart means a
second email on a paused thread cannot clobber the run waiting for approval.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from sc_core.infra.db import Database, ForeignKeyViolation
from sc_core.schema.base import StrictModel
from sc_core.shared.idempotency import new_id
from sc_core.shared.time import utc_now

CaseKind = Literal["rfq", "eta", "inbound", "unlinked", "receipt", "planning", "invoice"]
CaseStatus = Literal["open", "awaiting_approval", "done", "rejected", "failed", "escalated"]
CaseEventKind = Literal[
    "event_received",
    "task_sent",
    "result",
    "approval_requested",
    "approval_resolved",
    "rule_fired",
    "escalated",
    "note",
]

TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "rejected", "failed"})

# Missing sentinel for optional updates (``None`` is a legitimate value for some columns).
_UNSET: Any = object()


class Case(StrictModel):
    case_id: str
    kind: CaseKind
    status: CaseStatus
    po_name: str | None = None
    partner_id: int | None = None
    conversation_id: str | None = None
    agent: str | None = None
    trace_id: str | None = None
    summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    next_action_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status not in TERMINAL_STATUSES


class CaseEvent(StrictModel):
    id: int
    case_id: str
    at: datetime
    kind: CaseEventKind
    payload: dict[str, Any]


def matches(case: Case, *, po_name: str | None, conversation_id: str | None) -> bool:
    """Whether new work on ``po_name`` / ``conversation_id`` belongs to ``case``."""
    if not case.is_open or po_name is None or case.po_name != po_name:
        return False
    if case.conversation_id is None or conversation_id is None:
        return True
    return case.conversation_id == conversation_id


@runtime_checkable
class CaseStore(Protocol):
    async def attach_or_create(
        self,
        *,
        kind: CaseKind,
        po_name: str | None,
        partner_id: int | None = None,
        conversation_id: str | None = None,
        agent: str | None = None,
    ) -> tuple[Case, bool]:
        """The open case this work belongs to, or a new one; ``True`` when created."""
        ...

    async def get(self, case_id: str) -> Case | None: ...

    async def update(
        self,
        case_id: str,
        *,
        status: CaseStatus | None = None,
        summary: str | None = None,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        partner_id: int | None = None,
        agent: str | None = None,
        next_action_at: datetime | None = _UNSET,
    ) -> Case:
        """Set the given columns (``None`` means "leave as is", except ``next_action_at``)."""
        ...

    async def add_event(
        self, case_id: str, kind: CaseEventKind, payload: dict[str, Any]
    ) -> int: ...

    async def events(self, case_id: str) -> list[CaseEvent]: ...

    async def open_for_po(self, po_name: str) -> list[Case]: ...

    async def list(
        self,
        *,
        status: CaseStatus | None = None,
        po_name: str | None = None,
        limit: int = 50,
    ) -> list[Case]: ...


def _pick(case: Case, *, po_name: str | None, conversation_id: str | None) -> Case | None:
    return case if matches(case, po_name=po_name, conversation_id=conversation_id) else None


# --- Postgres -------------------------------------------------------------------------

_CASE_COLUMNS = (
    "case_id, kind, status, po_name, partner_id, conversation_id, agent, trace_id, summary, "
    "created_at, updated_at, next_action_at"
)


class PostgresCaseStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def attach_or_create(
        self,
        *,
        kind: CaseKind,
        po_name: str | None,
        partner_id: int | None = None,
        conversation_id: str | None = None,
        agent: str | None = None,
    ) -> tuple[Case, bool]:
        if po_name is not None:
            for candidate in await self.open_for_po(po_name):
                found = _pick(candidate, po_name=po_name, conversation_id=conversation_id)
                if found is not None:
                    return await self._fill_in(found, partner_id, conversation_id), False
        case = Case(
            case_id=new_id("case"),
            kind=kind,
            status="open",
            po_name=po_name,
            partner_id=partner_id,
            conversation_id=conversation_id,
            agent=agent,
        )
        await self._db.execute(
            "INSERT INTO cases (case_id, kind, status, po_name, partner_id, conversation_id, "
            "agent, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                case.case_id,
                case.kind,
                case.status,
                case.po_name,
                case.partner_id,
                case.conversation_id,
                case.agent,
                case.created_at,
                case.updated_at,
            ),
        )
        return case, True

    async def _fill_in(
        self, case: Case, partner_id: int | None, conversation_id: str | None
    ) -> Case:
        """An attached event may teach the case its partner or conversation."""
        if (partner_id is None or case.partner_id is not None) and (
            conversation_id is None or case.conversation_id is not None
        ):
            return case
        return await self.update(
            case.case_id,
            partner_id=partner_id if case.partner_id is None else None,
            conversation_id=conversation_id if case.conversation_id is None else None,
        )

    async def get(self, case_id: str) -> Case | None:
        row = await self._db.fetch_one(
            f"SELECT {_CASE_COLUMNS} FROM cases WHERE case_id = %s", (case_id,)
        )
        return Case(**row) if row else None

    async def update(
        self,
        case_id: str,
        *,
        status: CaseStatus | None = None,
        summary: str | None = None,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        partner_id: int | None = None,
        agent: str | None = None,
        next_action_at: datetime | None = _UNSET,
    ) -> Case:
        sets: list[str] = ["updated_at = %s"]
        params: list[Any] = [utc_now()]
        for column, value in (
            ("status", status),
            ("summary", summary),
            ("trace_id", trace_id),
            ("conversation_id", conversation_id),
            ("partner_id", partner_id),
            ("agent", agent),
        ):
            if value is not None:
                sets.append(f"{column} = %s")
                params.append(value)
        if next_action_at is not _UNSET:
            sets.append("next_action_at = %s")
            params.append(next_action_at)
        params.append(case_id)
        updated = await self._db.execute(
            f"UPDATE cases SET {', '.join(sets)} WHERE case_id = %s", tuple(params)
        )
        if updated != 1:
            raise KeyError(f"unknown case {case_id}")
        case = await self.get(case_id)
        assert case is not None
        return case

    async def add_event(self, case_id: str, kind: CaseEventKind, payload: dict[str, Any]) -> int:
        try:
            row = await self._db.fetch_one(
                "INSERT INTO case_events (case_id, kind, payload) VALUES (%s, %s, %s::jsonb) "
                "RETURNING id",
                (case_id, kind, json.dumps(payload, default=str)),
            )
        except ForeignKeyViolation as exc:
            raise KeyError(f"unknown case {case_id}") from exc
        assert row is not None
        return int(row["id"])

    async def events(self, case_id: str) -> list[CaseEvent]:
        rows = await self._db.fetch_all(
            "SELECT id, case_id, at, kind, payload FROM case_events WHERE case_id = %s ORDER BY id",
            (case_id,),
        )
        return [CaseEvent(**row) for row in rows]

    async def open_for_po(self, po_name: str) -> list[Case]:
        rows = await self._db.fetch_all(
            f"SELECT {_CASE_COLUMNS} FROM cases WHERE po_name = %s AND status <> ALL(%s) "
            "ORDER BY created_at DESC",
            (po_name, list(TERMINAL_STATUSES)),
        )
        return [Case(**row) for row in rows]

    async def list(
        self,
        *,
        status: CaseStatus | None = None,
        po_name: str | None = None,
        limit: int = 50,
    ) -> list[Case]:
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append("status = %s")
            params.append(status)
        if po_name is not None:
            where.append("po_name = %s")
            params.append(po_name)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)
        rows = await self._db.fetch_all(
            f"SELECT {_CASE_COLUMNS} FROM cases {clause} ORDER BY updated_at DESC LIMIT %s",
            tuple(params),
        )
        return [Case(**row) for row in rows]


# --- in memory (tests, e2e harnesses) -------------------------------------------------


class MemoryCaseStore:
    def __init__(self) -> None:
        self.cases: dict[str, Case] = {}
        self.case_events: list[CaseEvent] = []

    async def attach_or_create(
        self,
        *,
        kind: CaseKind,
        po_name: str | None,
        partner_id: int | None = None,
        conversation_id: str | None = None,
        agent: str | None = None,
    ) -> tuple[Case, bool]:
        if po_name is not None:
            for candidate in await self.open_for_po(po_name):
                found = _pick(candidate, po_name=po_name, conversation_id=conversation_id)
                if found is not None:
                    return (
                        await self.update(
                            found.case_id,
                            partner_id=partner_id if found.partner_id is None else None,
                            conversation_id=(
                                conversation_id if found.conversation_id is None else None
                            ),
                        ),
                        False,
                    )
        case = Case(
            case_id=new_id("case"),
            kind=kind,
            status="open",
            po_name=po_name,
            partner_id=partner_id,
            conversation_id=conversation_id,
            agent=agent,
        )
        self.cases[case.case_id] = case
        return case, True

    async def get(self, case_id: str) -> Case | None:
        return self.cases.get(case_id)

    async def update(
        self,
        case_id: str,
        *,
        status: CaseStatus | None = None,
        summary: str | None = None,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        partner_id: int | None = None,
        agent: str | None = None,
        next_action_at: datetime | None = _UNSET,
    ) -> Case:
        if case_id not in self.cases:
            raise KeyError(f"unknown case {case_id}")
        changes: dict[str, Any] = {"updated_at": utc_now()}
        for column, value in (
            ("status", status),
            ("summary", summary),
            ("trace_id", trace_id),
            ("conversation_id", conversation_id),
            ("partner_id", partner_id),
            ("agent", agent),
        ):
            if value is not None:
                changes[column] = value
        if next_action_at is not _UNSET:
            changes["next_action_at"] = next_action_at
        self.cases[case_id] = self.cases[case_id].model_copy(update=changes)
        return self.cases[case_id]

    async def add_event(self, case_id: str, kind: CaseEventKind, payload: dict[str, Any]) -> int:
        if case_id not in self.cases:
            raise KeyError(f"unknown case {case_id}")
        # Round-trip through JSON so the payload is as plain as the Postgres column.
        event = CaseEvent(
            id=len(self.case_events) + 1,
            case_id=case_id,
            at=utc_now(),
            kind=kind,
            payload=json.loads(json.dumps(payload, default=str)),
        )
        self.case_events.append(event)
        return event.id

    async def events(self, case_id: str) -> list[CaseEvent]:
        return [e for e in self.case_events if e.case_id == case_id]

    async def open_for_po(self, po_name: str) -> list[Case]:
        found = [c for c in self.cases.values() if c.po_name == po_name and c.is_open]
        return sorted(found, key=lambda c: c.created_at, reverse=True)

    async def list(
        self,
        *,
        status: CaseStatus | None = None,
        po_name: str | None = None,
        limit: int = 50,
    ) -> list[Case]:
        found = [
            c
            for c in self.cases.values()
            if (status is None or c.status == status) and (po_name is None or c.po_name == po_name)
        ]
        return sorted(found, key=lambda c: c.updated_at, reverse=True)[:limit]
