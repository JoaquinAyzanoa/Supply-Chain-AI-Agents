"""What the mail tables (migration 002) tell the orchestrator.

``mail_outbound`` says which Outlook conversation an agent's sent message
opened (so the supplier's reply attaches to the same case) and when we last
wrote about an order; ``mail_processed`` says when a supplier last wrote
about it. Only identifiers, names and timestamps live there.
"""

from __future__ import annotations

from datetime import date

from sc_core.infra.db import Database


class PostgresConversationLookup:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def conversation_for(self, graph_message_id: str) -> str | None:
        row = await self._db.fetch_one(
            "SELECT conversation_id FROM mail_outbound WHERE graph_message_id = %s",
            (graph_message_id,),
        )
        return row["conversation_id"] if row else None


class PostgresMailActivity:
    """Per order: (last email we sent, last supplier message linked), as local dates."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def contacts(self) -> dict[str, tuple[date | None, date | None]]:
        rows = await self._db.fetch_all(
            "SELECT po_name, max(sent_at) AS last_out, NULL::timestamptz AS last_in "
            "FROM mail_outbound GROUP BY po_name "
            "UNION ALL "
            "SELECT po_name, NULL, max(processed_at) FROM mail_processed "
            "WHERE po_name IS NOT NULL AND outcome = 'linked' GROUP BY po_name"
        )
        result: dict[str, tuple[date | None, date | None]] = {}
        for row in rows:
            out, inbound = result.get(row["po_name"], (None, None))
            if row["last_out"] is not None:
                out = row["last_out"].date()
            if row["last_in"] is not None:
                inbound = row["last_in"].date()
            result[row["po_name"]] = (out, inbound)
        return result


class MemoryMailActivity:
    def __init__(self, contacts: dict[str, tuple[date | None, date | None]] | None = None) -> None:
        self.known = dict(contacts or {})

    async def contacts(self) -> dict[str, tuple[date | None, date | None]]:
        return dict(self.known)


class MemoryConversationLookup:
    def __init__(self, known: dict[str, str] | None = None) -> None:
        self.known = dict(known or {})

    async def conversation_for(self, graph_message_id: str) -> str | None:
        return self.known.get(graph_message_id)
