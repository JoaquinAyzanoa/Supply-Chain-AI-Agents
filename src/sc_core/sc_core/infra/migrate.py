"""Plain-SQL migration runner for the application database.

The schema we own is small (mail links, cases, planning tables, token
cache), so a directory of numbered ``.sql`` files applied in order is enough
and keeps every change reviewable as SQL. Applied versions are recorded in
``schema_migrations``; a run is idempotent and safe to execute on every
service start because it takes a Postgres advisory lock first.

File naming: ``NNN_description.sql`` (``001_mail_token_cache.sql``). Each
file runs inside one transaction together with its bookkeeping row, so a
failing migration leaves nothing half-applied.

Usage: ``python -m sc_core.infra.migrate`` or :func:`apply_migrations` from a
startup hook (wrap in ``asyncio.to_thread`` — this module is synchronous).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
from loguru import logger

from sc_core.shared.errors import ConfigurationError

_NAME = re.compile(r"^(?P<version>\d{3,})_(?P<slug>[a-z0-9_]+)\.sql$")
# Arbitrary constant; must be the same in every process that migrates this database.
_LOCK_KEY = 7_412_009

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    name       text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True, order=True)
class Migration:
    version: str
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def discover(directory: Path) -> list[Migration]:
    """List migrations in ``directory`` sorted by version.

    Raises ``ConfigurationError`` for a malformed file name or a duplicate
    version, because either means two developers will disagree about order.
    """
    if not directory.is_dir():
        raise ConfigurationError(f"migrations directory not found: {directory}")
    found: dict[str, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _NAME.match(path.name)
        if not match:
            raise ConfigurationError(
                f"bad migration file name {path.name!r}; expected NNN_snake_case.sql"
            )
        version = match["version"]
        if version in found:
            raise ConfigurationError(
                f"duplicate migration version {version}: {found[version].path.name} and {path.name}"
            )
        found[version] = Migration(version=version, name=path.stem, path=path)
    return sorted(found.values())


def applied_versions(conn: psycopg.Connection) -> set[str]:
    conn.execute(_BOOTSTRAP)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def apply_migrations(dsn: str, directory: Path) -> list[str]:
    """Apply pending migrations. Returns the versions applied in this run."""
    migrations = discover(directory)
    applied: list[str] = []
    with psycopg.connect(dsn) as conn:
        # Serialize concurrent starters (several containers booting at once).
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            done = applied_versions(conn)
            conn.commit()
            for migration in migrations:
                if migration.version in done:
                    continue
                with conn.transaction():
                    conn.execute(migration.sql)  # type: ignore[arg-type]
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                        (migration.version, migration.name),
                    )
                applied.append(migration.version)
                logger.bind(version=migration.version).info("applied migration {}", migration.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
            conn.commit()
    if not applied:
        logger.info("migrations up to date ({} known)", len(migrations))
    return applied


def main(argv: list[str] | None = None) -> int:
    from sc_core.infra.logger import configure_logging
    from sc_core.infra.settings import get_settings

    settings = get_settings()
    configure_logging(settings)
    directory = Path((argv or sys.argv[1:] or [settings.app_db.migrations_dir])[0])
    apply_migrations(str(settings.app_db.dsn), directory)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
