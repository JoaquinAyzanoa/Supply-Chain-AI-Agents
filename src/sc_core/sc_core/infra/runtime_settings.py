"""Read the current runtime settings with a short cache.

Every service holds one :class:`RuntimeSettingsReader`. ``current()`` returns
the latest ``settings_history`` row, re-read at most every ``ttl_seconds``
(60 by default), so a change made in the Control Tower reaches the agents
within a minute and costs one small query per minute per process. Without a
database (tests, tools) it returns the defaults derived from the environment.
"""

from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger

from sc_core.infra.db import Database
from sc_core.schema.runtime_settings import RuntimeSettings


class RuntimeSettingsReader:
    def __init__(
        self,
        db: Database | None = None,
        *,
        defaults: RuntimeSettings | None = None,
        ttl_seconds: float = 60.0,
    ) -> None:
        self._db = db
        self._defaults = defaults or RuntimeSettings()
        self._ttl = ttl_seconds
        self._cached: RuntimeSettings | None = None
        self._version: int = 0
        self._read_at: float = 0.0

    @property
    def defaults(self) -> RuntimeSettings:
        return self._defaults

    @property
    def version(self) -> int:
        """The version last read (0 = the environment's defaults)."""
        return self._version

    async def current(self) -> RuntimeSettings:
        """The effective settings: the latest saved version, else the defaults."""
        if self._db is None:
            return self._defaults
        now = time.monotonic()
        if self._cached is not None and now - self._read_at < self._ttl:
            return self._cached
        try:
            row = await self._db.fetch_one(
                "SELECT version, settings FROM settings_history ORDER BY version DESC LIMIT 1"
            )
        except Exception as exc:  # a stale value beats a failed run
            logger.warning("runtime settings unavailable, keeping the last value: {}", exc)
            return self._cached or self._defaults
        self._read_at = now
        if row is None:
            self._cached, self._version = self._defaults, 0
        else:
            self._cached = _parse(row["settings"], fallback=self._defaults)
            self._version = int(row["version"])
        return self._cached

    def invalidate(self) -> None:
        """Forget the cache; the next ``current()`` re-reads (after a save in this process)."""
        self._read_at = 0.0


class MemoryRuntimeSettingsReader(RuntimeSettingsReader):
    """A reader tests drive by hand."""

    def __init__(self, value: RuntimeSettings | None = None) -> None:
        super().__init__(None, defaults=value)
        self.reads = 0

    def set(self, value: RuntimeSettings) -> None:
        self._defaults = value

    async def current(self) -> RuntimeSettings:
        self.reads += 1
        return self._defaults


def _parse(raw: Any, *, fallback: RuntimeSettings) -> RuntimeSettings:
    data = json.loads(raw) if isinstance(raw, str) else raw
    try:
        return RuntimeSettings.model_validate(data)
    except ValueError as exc:  # an older row shape: never break the agents over it
        logger.warning("ignoring an unreadable settings_history row: {}", exc)
        return fallback
