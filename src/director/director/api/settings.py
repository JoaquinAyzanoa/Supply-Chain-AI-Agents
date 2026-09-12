"""Settings people change at runtime, versioned in ``settings_history``.

``RuntimeSettings`` is the editable subset: the model per agent, the
follow-up policy, the auto-send suppliers and the planning defaults.
Everything else stays in ``.env``. Each ``PUT`` appends a version; the
agents read the current version through a cached reader (P8-S3), so a
change takes effect without a restart.
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
from sc_core.infra.db import Database
from sc_core.schema.base import StrictModel

router = APIRouter(prefix="/settings", tags=["settings"])


class RuntimeSettings(StrictModel):
    """What the Control Tower may change. Defaults mirror ``Settings``."""

    model_by_agent: dict[str, str] = Field(default_factory=dict, description="agent -> model name")
    rfq_no_reply_days: list[int] = [3, 7]
    po_eta_request_before_days: int = Field(default=5, ge=0)
    po_late_days: list[int] = [1, 4]
    approval_stale_days: int = Field(default=2, ge=0)
    approval_expire_days: int = Field(default=7, ge=0)
    max_actions_per_run: int = Field(default=20, ge=1)
    auto_send_partner_ids: list[int] = []
    planning_service_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    planning_review_period_days: int = Field(default=7, ge=1)
    planning_max_coverage_days: int = Field(default=120, ge=1)


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
) -> SettingsVersion:
    current = await store.current()
    if current is None:  # nothing saved yet: the defaults, as version 0
        from sc_core.shared.time import utc_now

        return SettingsVersion(
            version=0, settings=RuntimeSettings(), changed_by="defaults", changed_at=utc_now()
        )
    return current


@router.put("", response_model=SettingsVersion)
async def put_settings(
    body: SettingsUpdate,
    principal: Principal = Admin,
    store: RuntimeSettingsStore = Injected(RuntimeSettingsStore),  # type: ignore[type-abstract]
) -> SettingsVersion:
    saved = await store.save(body.settings, changed_by=principal.email, note=body.note)
    logger.bind(version=saved.version, by=principal.email).info("runtime settings saved")
    return saved


@router.get("/history", response_model=list[SettingsVersion])
async def settings_history(
    _: Principal = Viewer,
    store: RuntimeSettingsStore = Injected(RuntimeSettingsStore),  # type: ignore[type-abstract]
) -> list[SettingsVersion]:
    return await store.history()
