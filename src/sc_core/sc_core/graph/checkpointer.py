"""LangGraph checkpointers.

``build_checkpointer(db)`` reuses the service's connection pool (autocommit,
dict rows, which is what the saver expects) and creates LangGraph's tables
once. ``memory_checkpointer()`` is for unit tests; it forgets everything
when the process ends, which is exactly what a test wants.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from sc_core.infra.db import Database


async def build_checkpointer(db: Database) -> AsyncPostgresSaver:
    """A Postgres saver on ``db``'s pool; the pool must already be open."""
    saver = AsyncPostgresSaver(db.pool)
    await saver.setup()  # idempotent: creates checkpoint tables and applies its migrations
    return saver


def memory_checkpointer() -> InMemorySaver:
    return InMemorySaver()
