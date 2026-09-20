"""Sync state in the application database (migration 002).

Three facts survive between runs: the Graph delta link per mailbox, the set
of message ids already handled, and the Message-IDs of what the agents sent
(for ``In-Reply-To`` matching). Nothing else about a message is kept.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sc_core.infra.db import Database

Outcome = str  # linked | unlinked | skipped


@runtime_checkable
class SyncState(Protocol):
    async def delta_link(self, mailbox: str) -> str | None: ...

    async def save_delta_link(self, mailbox: str, delta_link: str, *, status: str) -> None: ...

    async def is_processed(self, graph_message_id: str) -> bool: ...

    async def mark_processed(
        self,
        graph_message_id: str,
        *,
        outcome: Outcome,
        po_name: str | None = None,
        case_id: str | None = None,
    ) -> None: ...

    async def record_outbound(
        self,
        *,
        graph_message_id: str,
        internet_message_id: str | None,
        conversation_id: str | None,
        po_name: str,
        case_id: str,
    ) -> None: ...

    async def outbound_po(self, internet_message_id: str) -> str | None: ...


class PostgresSyncState:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def delta_link(self, mailbox: str) -> str | None:
        row = await self._db.fetch_one(
            "SELECT delta_link FROM mail_sync_state WHERE mailbox = %s", (mailbox,)
        )
        return row["delta_link"] if row else None

    async def save_delta_link(self, mailbox: str, delta_link: str, *, status: str) -> None:
        await self._db.execute(
            "INSERT INTO mail_sync_state (mailbox, delta_link, last_run_at, last_status) "
            "VALUES (%s, %s, now(), %s) ON CONFLICT (mailbox) DO UPDATE SET "
            "delta_link = EXCLUDED.delta_link, last_run_at = now(), "
            "last_status = EXCLUDED.last_status",
            (mailbox, delta_link, status),
        )

    async def is_processed(self, graph_message_id: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 AS one FROM mail_processed WHERE graph_message_id = %s", (graph_message_id,)
        )
        return row is not None

    async def mark_processed(
        self,
        graph_message_id: str,
        *,
        outcome: Outcome,
        po_name: str | None = None,
        case_id: str | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO mail_processed (graph_message_id, outcome, po_name, case_id) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (graph_message_id) DO NOTHING",
            (graph_message_id, outcome, po_name, case_id),
        )

    async def record_outbound(
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

    async def outbound_po(self, internet_message_id: str) -> str | None:
        row = await self._db.fetch_one(
            "SELECT po_name FROM mail_outbound WHERE internet_message_id = %s",
            (internet_message_id,),
        )
        return row["po_name"] if row else None


class MemorySyncState:
    """In-memory twin for unit tests; exposes its tables for assertions."""

    def __init__(self) -> None:
        self.delta_links: dict[str, str] = {}
        self.statuses: dict[str, str] = {}
        self.processed: dict[str, dict[str, Any]] = {}
        self.outbound: dict[str, dict[str, Any]] = {}

    async def delta_link(self, mailbox: str) -> str | None:
        return self.delta_links.get(mailbox)

    async def save_delta_link(self, mailbox: str, delta_link: str, *, status: str) -> None:
        self.delta_links[mailbox] = delta_link
        self.statuses[mailbox] = status

    async def is_processed(self, graph_message_id: str) -> bool:
        return graph_message_id in self.processed

    async def mark_processed(
        self,
        graph_message_id: str,
        *,
        outcome: Outcome,
        po_name: str | None = None,
        case_id: str | None = None,
    ) -> None:
        self.processed.setdefault(
            graph_message_id, {"outcome": outcome, "po_name": po_name, "case_id": case_id}
        )

    async def record_outbound(
        self,
        *,
        graph_message_id: str,
        internet_message_id: str | None,
        conversation_id: str | None,
        po_name: str,
        case_id: str,
    ) -> None:
        self.outbound.setdefault(
            graph_message_id,
            {
                "internet_message_id": internet_message_id,
                "conversation_id": conversation_id,
                "po_name": po_name,
                "case_id": case_id,
            },
        )

    async def outbound_po(self, internet_message_id: str) -> str | None:
        for row in self.outbound.values():
            if row["internet_message_id"] == internet_message_id:
                return str(row["po_name"])
        return None
