"""Internal purchase requests (phase 11 S6): who asked, which RFQs came out of it.

An employee's email to the purchasing mailbox becomes one or more RFQs after
an ``internal_request`` approval. This table keeps the link between the
request (the message to reply to, the requester's address) and the orders,
so the playbook can tell the requester when the order is confirmed and when
the goods arrive. No subject, body or name is stored: only identifiers.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from sc_core.infra.db import Database
from sc_core.schema.base import StrictModel


class InternalRequest(StrictModel):
    id: int | None = None
    case_id: str
    graph_message_id: str = Field(description="the request email; status replies answer it")
    requester_address: str
    po_names: list[str] = []
    need_date: date | None = None
    items: int = 0
    status: str = "open"  # open | done | rejected
    created_at: datetime | None = None
    updated_at: datetime | None = None


@runtime_checkable
class InternalRequestStore(Protocol):
    async def save(self, request: InternalRequest) -> InternalRequest: ...

    async def for_po(self, po_name: str) -> InternalRequest | None: ...

    async def set_status(self, request_id: int, status: str) -> None: ...


def _request(row: dict[str, Any]) -> InternalRequest:
    return InternalRequest(
        id=int(row["id"]),
        case_id=str(row["case_id"]),
        graph_message_id=str(row["graph_message_id"]),
        requester_address=str(row["requester_address"]),
        po_names=list(row.get("po_names") or []),
        need_date=row.get("need_date"),
        items=int(row.get("items") or 0),
        status=str(row.get("status") or "open"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class PostgresInternalRequestStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, request: InternalRequest) -> InternalRequest:
        row = await self._db.fetch_one(
            "INSERT INTO internal_requests (case_id, graph_message_id, requester_address, "
            "po_names, need_date, items, status) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (graph_message_id) DO UPDATE SET po_names = EXCLUDED.po_names, "
            "need_date = EXCLUDED.need_date, items = EXCLUDED.items, status = EXCLUDED.status, "
            "updated_at = now() RETURNING *",
            (
                request.case_id,
                request.graph_message_id,
                request.requester_address,
                request.po_names,
                request.need_date,
                request.items,
                request.status,
            ),
        )
        assert row is not None
        return _request(row)

    async def for_po(self, po_name: str) -> InternalRequest | None:
        row = await self._db.fetch_one(
            "SELECT * FROM internal_requests WHERE %s = ANY(po_names) "
            "ORDER BY created_at DESC LIMIT 1",
            (po_name,),
        )
        return _request(row) if row else None

    async def set_status(self, request_id: int, status: str) -> None:
        await self._db.execute(
            "UPDATE internal_requests SET status = %s, updated_at = now() WHERE id = %s",
            (status, request_id),
        )


class MemoryInternalRequestStore:
    def __init__(self) -> None:
        self.rows: dict[int, InternalRequest] = {}

    async def save(self, request: InternalRequest) -> InternalRequest:
        for existing in self.rows.values():
            if existing.graph_message_id == request.graph_message_id:
                saved = request.model_copy(update={"id": existing.id})
                self.rows[existing.id or 0] = saved
                return saved
        new_id = max(self.rows, default=0) + 1
        saved = request.model_copy(update={"id": new_id})
        self.rows[new_id] = saved
        return saved

    async def for_po(self, po_name: str) -> InternalRequest | None:
        for request in reversed(list(self.rows.values())):
            if po_name in request.po_names:
                return request
        return None

    async def set_status(self, request_id: int, status: str) -> None:
        if request_id in self.rows:
            self.rows[request_id] = self.rows[request_id].model_copy(update={"status": status})


__all__ = [
    "InternalRequest",
    "InternalRequestStore",
    "MemoryInternalRequestStore",
    "PostgresInternalRequestStore",
]
