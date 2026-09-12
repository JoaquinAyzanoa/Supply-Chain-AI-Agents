"""Async access to the application database.

One ``Database`` per process wraps a ``psycopg_pool.AsyncConnectionPool``.
Services open it at startup and close it at shutdown; repositories borrow a
connection per operation. Queries are plain SQL: the tables are small and
few, and the schema is owned by ``migrations/``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from sc_core.infra.settings import AppDbCfg


class Database:
    def __init__(self, cfg: AppDbCfg) -> None:
        self._pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
            str(cfg.dsn),
            min_size=cfg.pool_min,
            max_size=cfg.pool_max,
            open=False,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )

    @property
    def pool(self) -> AsyncConnectionPool[AsyncConnection[dict[str, Any]]]:
        """The underlying pool, for libraries that manage their own connections (LangGraph)."""
        return self._pool

    async def open(self) -> None:
        await self._pool.open()

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[dict[str, Any]]]:
        """A pooled connection in autocommit mode; wrap in ``transaction()`` when needed."""
        async with self._pool.connection() as conn:
            yield conn

    async def fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        async with self.connection() as conn:
            cur = await conn.execute(query, params)
            row = await cur.fetchone()
            return dict(row) if row is not None else None

    async def fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with self.connection() as conn:
            cur = await conn.execute(query, params)
            return [dict(r) for r in await cur.fetchall()]

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> int:
        """Run a statement and return the affected row count."""
        async with self.connection() as conn:
            cur = await conn.execute(query, params)
            return cur.rowcount
