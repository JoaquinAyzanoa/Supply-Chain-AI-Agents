"""Fixtures for integration tests (require Docker)."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest

# Testcontainers starts a "Ryuk" reaper container to clean up after tests. On
# Docker Desktop for Windows its port mapping is often unavailable, which
# aborts every container start. Containers are context-managed below, so the
# reaper is not needed; disable it unless the caller decided otherwise.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """A throwaway Postgres 16 started with testcontainers."""
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # older testcontainers layout
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver=None) as pg:
        yield pg.get_connection_url()


@pytest.fixture
def fresh_postgres_dsn(postgres_dsn: str) -> Iterator[str]:
    """A brand-new database on the session container, so tests never share state."""
    import psycopg
    from psycopg import sql

    name = f"t_{uuid4().hex[:12]}"
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    base = postgres_dsn.rsplit("/", 1)[0]
    yield f"{base}/{name}"
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def redis_dsn() -> Iterator[str]:
    try:
        from testcontainers.community.redis import RedisContainer
    except ImportError:
        from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as rc:
        host = rc.get_container_host_ip()
        port = rc.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"
