"""Supplier profiles in the application database (``supplier_profiles``)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.profiles import SupplierProfile


@runtime_checkable
class ProfileReader(Protocol):
    async def get(self, partner_id: int) -> SupplierProfile | None: ...


@runtime_checkable
class ProfileStore(ProfileReader, Protocol):
    async def save(self, profile: SupplierProfile, *, by: str | None) -> SupplierProfile: ...

    async def update_facts(self, partner_id: int, facts: dict[str, Any]) -> None: ...


def _row(row: dict[str, Any]) -> SupplierProfile:
    def js(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    return SupplierProfile(
        partner_id=int(row["partner_id"]),
        language=row.get("language"),
        formality=row.get("formality"),
        greeting=row.get("greeting"),
        sign_off=row.get("sign_off"),
        contacts=[str(c) for c in js(row.get("contacts")) or []],
        notes=str(row.get("notes") or ""),
        facts=js(row.get("facts")) or {},
        updated_at=row.get("updated_at"),
        updated_by=row.get("updated_by"),
    )


class PostgresProfileStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, partner_id: int) -> SupplierProfile | None:
        row = await self._db.fetch_one(
            "SELECT * FROM supplier_profiles WHERE partner_id = %s", (partner_id,)
        )
        return _row(row) if row else None

    async def save(self, profile: SupplierProfile, *, by: str | None) -> SupplierProfile:
        row = await self._db.fetch_one(
            "INSERT INTO supplier_profiles (partner_id, language, formality, greeting, sign_off, "
            "contacts, notes, updated_by) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s) "
            "ON CONFLICT (partner_id) DO UPDATE SET language = EXCLUDED.language, "
            "formality = EXCLUDED.formality, greeting = EXCLUDED.greeting, "
            "sign_off = EXCLUDED.sign_off, contacts = EXCLUDED.contacts, notes = EXCLUDED.notes, "
            "updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING *",
            (
                profile.partner_id,
                profile.language,
                profile.formality,
                profile.greeting,
                profile.sign_off,
                json.dumps(profile.contacts),
                profile.notes,
                by,
            ),
        )
        assert row is not None
        return _row(row)

    async def update_facts(self, partner_id: int, facts: dict[str, Any]) -> None:
        """Merge agent-maintained facts without touching what people wrote."""
        await self._db.execute(
            "INSERT INTO supplier_profiles (partner_id, facts) VALUES (%s, %s::jsonb) "
            "ON CONFLICT (partner_id) DO UPDATE SET "
            "facts = supplier_profiles.facts || EXCLUDED.facts",
            (partner_id, json.dumps(facts, default=str)),
        )


class MemoryProfileStore:
    def __init__(self) -> None:
        self.rows: dict[int, SupplierProfile] = {}

    async def get(self, partner_id: int) -> SupplierProfile | None:
        return self.rows.get(partner_id)

    async def save(self, profile: SupplierProfile, *, by: str | None) -> SupplierProfile:
        saved = profile.model_copy(update={"updated_by": by, "updated_at": datetime.now()})
        self.rows[profile.partner_id] = saved
        return saved

    async def update_facts(self, partner_id: int, facts: dict[str, Any]) -> None:
        current = self.rows.get(partner_id) or SupplierProfile(partner_id=partner_id)
        self.rows[partner_id] = current.model_copy(update={"facts": {**current.facts, **facts}})
