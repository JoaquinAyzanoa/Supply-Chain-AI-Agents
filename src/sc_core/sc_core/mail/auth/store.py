"""Token cache stores.

The MSAL cache (which contains refresh tokens) is serialised as a string.
Postgres is the production choice in delegated mode so every container
shares one login; a file is convenient for local one-off scripts; memory is
for tests. Treat the blob as a secret: never log it.
"""

from __future__ import annotations

from pathlib import Path

import psycopg


class MemoryTokenCacheStore:
    def __init__(self) -> None:
        self._blob: str | None = None

    def load(self) -> str | None:
        return self._blob

    def save(self, blob: str) -> None:
        self._blob = blob

    def clear(self) -> None:
        self._blob = None


class FileTokenCacheStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> str | None:
        return self._path.read_text(encoding="utf-8") if self._path.exists() else None

    def save(self, blob: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(blob, encoding="utf-8")

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


class PostgresTokenCacheStore:
    """Single-row table ``mail_token_cache`` created by migration 001."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def load(self) -> str | None:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute("SELECT blob FROM mail_token_cache WHERE id = 1").fetchone()
        return row[0] if row else None

    def save(self, blob: str) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO mail_token_cache (id, blob, updated_at) VALUES (1, %s, now()) "
                "ON CONFLICT (id) DO UPDATE SET blob = EXCLUDED.blob, updated_at = now()",
                (blob,),
            )

    def clear(self) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute("DELETE FROM mail_token_cache WHERE id = 1")
