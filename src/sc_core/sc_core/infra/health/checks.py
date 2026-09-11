"""Ready-made health checks for the infrastructure every service shares.

Each factory returns an async callable suitable for
``HealthRegistry.register``. They open a fresh connection per call on
purpose: a pooled connection can look healthy while the pool itself is
exhausted, and readiness probes are infrequent enough that the cost is
irrelevant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import redis.asyncio as redis_async

from sc_core.infra.health.types import HealthCheck

if TYPE_CHECKING:
    from sc_core.mail.protocol import MailClient
    from sc_core.odoo.client import OdooClient


def postgres(dsn: str, *, connect_timeout: int = 3) -> HealthCheck:
    async def check() -> None:
        async with await psycopg.AsyncConnection.connect(
            dsn, connect_timeout=connect_timeout
        ) as conn:
            await conn.execute("SELECT 1")

    check.__name__ = "postgres"
    return check


def redis(dsn: str, *, timeout: float = 3.0) -> HealthCheck:
    async def check() -> None:
        client = redis_async.from_url(dsn, socket_connect_timeout=timeout, socket_timeout=timeout)
        try:
            if not await client.ping():
                raise ConnectionError("redis did not answer PING")
        finally:
            await client.aclose()

    check.__name__ = "redis"
    return check


def odoo(client: OdooClient) -> HealthCheck:
    """Odoo answers ``common.version`` and the bot's credentials still log in."""

    async def check() -> None:
        await client.version()
        await client.uid()

    check.__name__ = "odoo"
    return check


def graph(client: MailClient) -> HealthCheck:
    """The mail session is valid and Graph answers for the mailbox."""

    async def check() -> None:
        await client.me()

    check.__name__ = "graph"
    return check
