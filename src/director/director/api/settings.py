"""Settings people change at runtime, versioned in ``settings_history``.

``RuntimeSettings`` (``sc_core.schema.runtime_settings``) is the editable
subset: the model per agent, the follow-up policy, the auto-send suppliers
and the planning defaults. Everything else stays in ``.env``. Each ``PUT``
appends a version; every service reads the current one through
``RuntimeSettingsReader`` (a minute of cache), so a change takes effect
without a restart. Version 0 is what the environment says.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.auth import Admin, Principal, Viewer
from sc_core.app.realtime import Realtime
from sc_core.infra.db import Database
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import Settings
from sc_core.schema.base import StrictModel
from sc_core.schema.runtime_settings import RuntimeSettings

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsVersion(StrictModel):
    version: int
    settings: RuntimeSettings
    changed_by: str
    note: str | None = None
    changed_at: datetime


class SettingsUpdate(StrictModel):
    settings: RuntimeSettings
    note: str | None = Field(default=None, max_length=300)


@runtime_checkable
class RuntimeSettingsStore(Protocol):
    async def current(self) -> SettingsVersion | None: ...

    async def save(
        self, settings: RuntimeSettings, *, changed_by: str, note: str | None
    ) -> SettingsVersion: ...

    async def history(self, limit: int = 20) -> list[SettingsVersion]: ...


class PostgresRuntimeSettingsStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def current(self) -> SettingsVersion | None:
        row = await self._db.fetch_one(
            "SELECT version, settings, changed_by, note, changed_at FROM settings_history "
            "ORDER BY version DESC LIMIT 1"
        )
        return _version(row) if row else None

    async def save(
        self, settings: RuntimeSettings, *, changed_by: str, note: str | None
    ) -> SettingsVersion:
        row = await self._db.fetch_one(
            "INSERT INTO settings_history (settings, changed_by, note) VALUES (%s::jsonb, %s, %s) "
            "RETURNING version, settings, changed_by, note, changed_at",
            (settings.model_dump_json(), changed_by, note),
        )
        assert row is not None
        return _version(row)

    async def history(self, limit: int = 20) -> list[SettingsVersion]:
        rows = await self._db.fetch_all(
            "SELECT version, settings, changed_by, note, changed_at FROM settings_history "
            "ORDER BY version DESC LIMIT %s",
            (limit,),
        )
        return [_version(r) for r in rows]


class MemoryRuntimeSettingsStore:
    def __init__(self) -> None:
        self.versions: list[SettingsVersion] = []

    async def current(self) -> SettingsVersion | None:
        return self.versions[-1] if self.versions else None

    async def save(
        self, settings: RuntimeSettings, *, changed_by: str, note: str | None
    ) -> SettingsVersion:
        from sc_core.shared.time import utc_now

        version = SettingsVersion(
            version=len(self.versions) + 1,
            settings=settings,
            changed_by=changed_by,
            note=note,
            changed_at=utc_now(),
        )
        self.versions.append(version)
        return version

    async def history(self, limit: int = 20) -> list[SettingsVersion]:
        return list(reversed(self.versions))[:limit]


def _version(row: dict[str, Any]) -> SettingsVersion:
    raw = row["settings"]
    data = json.loads(raw) if isinstance(raw, str) else raw
    return SettingsVersion(
        version=int(row["version"]),
        settings=RuntimeSettings.model_validate(data),
        changed_by=str(row["changed_by"]),
        note=row.get("note"),
        changed_at=row["changed_at"],
    )


@router.get("", response_model=SettingsVersion)
async def get_settings(
    _: Principal = Viewer,
    store: RuntimeSettingsStore = Injected(RuntimeSettingsStore),  # type: ignore[type-abstract]
    settings: Settings = Injected(Settings),
) -> SettingsVersion:
    current = await store.current()
    if current is None:  # nothing saved yet: what the environment says, as version 0
        from sc_core.shared.time import utc_now

        return SettingsVersion(
            version=0,
            settings=RuntimeSettings.from_settings(settings),
            changed_by="environment",
            changed_at=utc_now(),
        )
    return current


@router.put("", response_model=SettingsVersion)
async def put_settings(
    body: SettingsUpdate,
    principal: Principal = Admin,
    store: RuntimeSettingsStore = Injected(RuntimeSettingsStore),  # type: ignore[type-abstract]
    reader: RuntimeSettingsReader = Injected(RuntimeSettingsReader),
    realtime: Realtime = Injected(Realtime),  # type: ignore[type-abstract]
) -> SettingsVersion:
    saved = await store.save(body.settings, changed_by=principal.email, note=body.note)
    reader.invalidate()  # this process sees it now; the others within a minute
    await realtime.publish(
        "settings_changed", {"version": saved.version, "changed_by": principal.email}
    )
    logger.bind(version=saved.version, by=principal.email).info("runtime settings saved")
    return saved


@router.get("/history", response_model=list[SettingsVersion])
async def settings_history(
    _: Principal = Viewer,
    store: RuntimeSettingsStore = Injected(RuntimeSettingsStore),  # type: ignore[type-abstract]
) -> list[SettingsVersion]:
    return await store.history()
