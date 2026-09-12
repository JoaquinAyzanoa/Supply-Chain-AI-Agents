"""Sync state, outbox, event inbox and run store against a real Postgres (migration 002)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from director.inbox import PostgresEventInbox
from mail_sync.state import PostgresSyncState
from sc_core.a2a.events import PostgresOutbox
from sc_core.infra.db import Database
from sc_core.infra.migrate import apply_migrations
from sc_core.infra.settings import AppDbCfg
from sc_core.schema.events import InboundMailLinked
from scheduler.runs import PostgresRunStore, RunRecord

pytestmark = pytest.mark.integration

MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"


@pytest.fixture
async def db(fresh_postgres_dsn: str) -> AsyncIterator[Database]:
    apply_migrations(fresh_postgres_dsn, MIGRATIONS)
    database = Database(AppDbCfg(dsn=fresh_postgres_dsn))  # type: ignore[arg-type]
    await database.open()
    yield database
    await database.close()


async def test_sync_state_persists_delta_and_dedupes(db: Database) -> None:
    state = PostgresSyncState(db)
    assert await state.delta_link("me") is None
    await state.save_delta_link("me", "https://graph/delta?token=1", status="ok")
    await state.save_delta_link("me", "https://graph/delta?token=2", status="ok")
    assert await state.delta_link("me") == "https://graph/delta?token=2"

    await state.mark_processed("AAMk1", outcome="linked", po_name="P00015", case_id="case_1")
    await state.mark_processed("AAMk1", outcome="unlinked")  # duplicate: ignored, not an error
    assert await state.is_processed("AAMk1") and not await state.is_processed("AAMk2")
    row = await db.fetch_one(
        "SELECT outcome, po_name FROM mail_processed WHERE graph_message_id = %s", ("AAMk1",)
    )
    assert row == {"outcome": "linked", "po_name": "P00015"}

    await state.record_outbound(
        graph_message_id="AAMkOut",
        internet_message_id="<rfq@outlook.com>",
        conversation_id="conv",
        po_name="P00015",
        case_id="case_1",
    )
    assert await state.outbound_po("<rfq@outlook.com>") == "P00015"
    assert await state.outbound_po("<other@x>") is None


async def test_outbox_roundtrip(db: Database) -> None:
    outbox = PostgresOutbox(db)
    await outbox.add("evt_1", "inbound_mail.linked", "http://director/events", b'{"a": 1}')
    await outbox.add("evt_1", "inbound_mail.linked", "http://director/events", b"dup")
    await outbox.add("evt_2", "scheduler.tick", "http://director/events", b'{"b": 2}')
    await outbox.record_failure("evt_1", "503")
    rows = await outbox.pending()
    assert [r["event_id"] for r in rows] == ["evt_1", "evt_2"]
    assert rows[0]["body"] == b'{"a": 1}' and rows[0]["attempts"] == 1
    await outbox.delete("evt_1")
    assert [r["event_id"] for r in await outbox.pending()] == ["evt_2"]


async def test_event_inbox_is_idempotent(db: Database) -> None:
    inbox = PostgresEventInbox(db)
    event = InboundMailLinked(
        source="mail_sync",
        case_id="case_1",
        po_id=1,
        po_name="P00015",
        graph_message_id="AAMk1",
        confidence="exact",
        rule="header",
    )
    assert await inbox.store(event) is True
    assert await inbox.store(event) is False
    row = await db.fetch_one("SELECT payload->>'po_name' AS po FROM event_inbox")
    assert row == {"po": "P00015"}


async def test_run_store_upserts_and_lists(db: Database) -> None:
    runs = PostgresRunStore(db)
    started = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    record = RunRecord(run_id="run_1", job_id="mail_sync", trigger="manual", started_at=started)
    await runs.save(record)
    record.status, record.http_status, record.finished_at = "ok", 200, started
    await runs.save(record)
    last = await runs.last("mail_sync")
    assert last is not None and last.status == "ok" and last.http_status == 200
    assert await runs.last("nope") is None
    assert [r.run_id for r in await runs.recent()] == ["run_1"]
