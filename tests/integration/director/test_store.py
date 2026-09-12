"""Case store against a real Postgres (migration 004)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from director.store import CaseStore, PostgresCaseStore
from sc_core.infra.db import Database
from tests.unit.director.store_suite import CHECKS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
async def test_postgres_store(db: Database, check: Callable[[CaseStore], Awaitable[None]]) -> None:
    await check(PostgresCaseStore(db))


async def test_case_events_reference_cases(db: Database) -> None:
    store = PostgresCaseStore(db)
    case, _ = await store.attach_or_create(kind="rfq", po_name="P00015")
    await store.add_event(case.case_id, "note", {"text": "hello"})
    row = await db.fetch_one(
        "SELECT payload->>'text' AS text FROM case_events WHERE case_id = %s", (case.case_id,)
    )
    assert row == {"text": "hello"}
