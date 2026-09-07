"""Migration runner against a real Postgres."""

from pathlib import Path

import psycopg
import pytest

from sc_core.infra.migrate import apply_migrations

pytestmark = pytest.mark.integration


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_apply_is_incremental_and_idempotent(fresh_postgres_dsn: str, tmp_path: Path) -> None:
    _write(tmp_path, "001_widgets.sql", "CREATE TABLE widgets (id serial PRIMARY KEY, name text);")
    assert apply_migrations(fresh_postgres_dsn, tmp_path) == ["001"]
    assert apply_migrations(fresh_postgres_dsn, tmp_path) == []

    _write(tmp_path, "002_widgets_color.sql", "ALTER TABLE widgets ADD COLUMN color text;")
    assert apply_migrations(fresh_postgres_dsn, tmp_path) == ["002"]

    with psycopg.connect(fresh_postgres_dsn) as conn:
        rows = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert rows == [("001", "001_widgets"), ("002", "002_widgets_color")]
        conn.execute("INSERT INTO widgets (name, color) VALUES ('a', 'red')")


def test_failed_migration_is_rolled_back(fresh_postgres_dsn: str, tmp_path: Path) -> None:
    _write(tmp_path, "001_ok.sql", "CREATE TABLE gadgets (id int);")
    _write(
        tmp_path, "002_broken.sql", "CREATE TABLE gizmos (id int); SELECT * FROM does_not_exist;"
    )
    with pytest.raises(psycopg.Error):
        apply_migrations(fresh_postgres_dsn, tmp_path)

    with psycopg.connect(fresh_postgres_dsn) as conn:
        versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        assert "001" in versions and "002" not in versions
        exists = conn.execute("SELECT to_regclass('gizmos')").fetchone()
        assert exists == (None,)  # the partial DDL of 002 was rolled back
