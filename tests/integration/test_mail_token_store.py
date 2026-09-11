"""The Postgres token cache store against a real database with migration 001 applied."""

from pathlib import Path

import pytest

from sc_core.infra.migrate import apply_migrations
from sc_core.mail.auth import PostgresTokenCacheStore

pytestmark = pytest.mark.integration

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def test_roundtrip(fresh_postgres_dsn: str) -> None:
    applied = apply_migrations(fresh_postgres_dsn, MIGRATIONS)
    assert "001" in applied
    store = PostgresTokenCacheStore(fresh_postgres_dsn)
    assert store.load() is None
    store.save('{"a": 1}')
    store.save('{"a": 2}')  # upsert, still one row
    assert store.load() == '{"a": 2}'
    store.clear()
    assert store.load() is None
