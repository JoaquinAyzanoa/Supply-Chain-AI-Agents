"""The ``mail_outbound`` table (migration 002): what the agents sent, by Message-ID.

mail_sync reads it to link replies (rule 4); the agents write it after a
send. Only identifiers and the order name are stored.
"""

from __future__ import annotations

from typing import Any, Protocol

from sc_core.infra.db import Database


class OutboundMailStore(Protocol):
    async def record(
        self,
        *,
        graph_message_id: str,
        internet_message_id: str | None,
        conversation_id: str | None,
        po_name: str,
        case_id: str,
    ) -> None: ...


class PostgresOutboundMailStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        graph_message_id: str,
        internet_message_id: str | None,
        conversation_id: str | None,
        po_name: str,
        case_id: str,
    ) -> None:
        await self._db.execute(
            "INSERT INTO mail_outbound "
            "(graph_message_id, internet_message_id, conversation_id, po_name, case_id) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (graph_message_id) DO NOTHING",
            (graph_message_id, internet_message_id, conversation_id, po_name, case_id),
        )


class MemoryOutboundMailStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **row: Any) -> None:
        if not any(r["graph_message_id"] == row["graph_message_id"] for r in self.rows):
            self.rows.append(dict(row))
