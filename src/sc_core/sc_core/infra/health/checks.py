"""Ready-made health checks for the infrastructure every service shares.

Each factory returns an async callable suitable for
``HealthRegistry.register``. They open a fresh connection per call on
purpose: a pooled connection can look healthy while the pool itself is
exhausted, and readiness probes are infrequent enough that the cost is
irrelevant.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
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


def langfuse(host: str) -> HealthCheck:
    """Langfuse answers its public health endpoint."""

    async def check() -> None:
        async with httpx.AsyncClient(timeout=5.0) as http:
            response = await http.get(f"{host.rstrip('/')}/api/public/health")
            response.raise_for_status()

    check.__name__ = "langfuse"
    return check


def llm_provider(base_url: str, api_key: str, *, cache_seconds: float = 300.0) -> HealthCheck:
    """The model provider accepts the key (``GET /models``), cached to avoid hammering it."""
    state: dict[str, float] = {"checked_at": 0.0}

    async def check() -> None:
        now = time.monotonic()
        if now - state["checked_at"] < cache_seconds:
            return
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.get(
                f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"}
            )
            response.raise_for_status()
        state["checked_at"] = now

    check.__name__ = "llm_provider"
    return check


def graph(client: MailClient) -> HealthCheck:
    """The mail session is valid and Graph answers for the mailbox."""

    async def check() -> None:
        await client.me()

    check.__name__ = "graph"
    return check
