"""ui_users and settings_history against a real Postgres (migration 006)."""

from __future__ import annotations

import pytest

from director.api.auth import PostgresUserStore, hash_password, verify_password
from director.api.settings import PostgresRuntimeSettingsStore, RuntimeSettings
from sc_core.infra.db import Database

pytestmark = pytest.mark.integration


async def test_users_upsert_and_login_stamp(db: Database) -> None:
    store = PostgresUserStore(db)
    user = await store.create(
        email="Ana@X.com", name="Ana", password_hash=hash_password("s3cret!!"), role="approver"
    )
    assert user.email == "ana@x.com" and user.role == "approver"
    again = await store.create(
        email="ana@x.com", name="Ana P", password_hash=hash_password("other!!!"), role="admin"
    )
    assert again.id == user.id and again.role == "admin" and again.name == "Ana P"
    found = await store.by_email("ANA@x.com")
    assert found is not None and verify_password("other!!!", found[1])
    await store.touch_login(user.id)
    assert [u.email for u in await store.list()] == ["ana@x.com"]
    assert await store.by_email("nobody@x.com") is None


async def test_settings_versions(db: Database) -> None:
    store = PostgresRuntimeSettingsStore(db)
    assert await store.current() is None
    first = await store.save(RuntimeSettings(), changed_by="adm@x.com", note="defaults")
    second = await store.save(
        RuntimeSettings(rfq_no_reply_days=[2, 5], model_by_agent={"director": "gpt-5.4"}),
        changed_by="adm@x.com",
        note=None,
    )
    assert (first.version, second.version) == (1, 2)
    current = await store.current()
    assert current is not None and current.settings.rfq_no_reply_days == [2, 5]
    assert current.settings.model_by_agent == {"director": "gpt-5.4"}
    assert [v.version for v in await store.history()] == [2, 1]
