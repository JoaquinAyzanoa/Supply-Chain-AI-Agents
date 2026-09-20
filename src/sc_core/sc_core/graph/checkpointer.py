"""LangGraph checkpointers.

``build_checkpointer(db)`` reuses the service's connection pool (autocommit,
dict rows, which is what the saver expects) and creates LangGraph's tables
once. ``memory_checkpointer()`` is for unit tests; it forgets everything
when the process ends, which is exactly what a test wants.
"""

from __future__ import annotations

import asyncio

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import errors

from sc_core.infra.db import Database

SETUP_ATTEMPTS = 5


async def build_checkpointer(db: Database) -> AsyncPostgresSaver:
    """A Postgres saver on ``db``'s pool; the pool must already be open."""
    saver = AsyncPostgresSaver(db.pool)
    await _setup(saver)
    return saver


async def _setup(saver: AsyncPostgresSaver) -> None:
    """Create LangGraph's tables and apply its migrations.

    ``setup()`` is idempotent but not safe against itself: on a fresh database every agent
    starts at once, two of them apply the same migration, and the loser dies on the
    migrations table's primary key. The winner's work is done by then, so the loser only
    has to try again: the next pass finds the migration recorded and skips it.
    """
    for attempt in range(1, SETUP_ATTEMPTS + 1):
        try:
            await saver.setup()
            return
        except (errors.UniqueViolation, errors.DuplicateTable, errors.DuplicateObject):
            if attempt == SETUP_ATTEMPTS:
                raise
            await asyncio.sleep(0.5 * attempt)


def memory_checkpointer() -> InMemorySaver:
    return InMemorySaver()
