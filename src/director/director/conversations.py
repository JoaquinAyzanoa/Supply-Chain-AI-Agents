"""Which Outlook conversation an agent's sent message opened (``mail_outbound``, migration 002).

The consolidate step uses it to teach a case its thread, so the supplier's
reply (linked by mail_sync with the same conversation id) attaches to the
same case.
"""

from __future__ import annotations

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


class MemoryConversationLookup:
    def __init__(self, known: dict[str, str] | None = None) -> None:
        self.known = dict(known or {})

    async def conversation_for(self, graph_message_id: str) -> str | None:
        return self.known.get(graph_message_id)
