"""A paused graph resumes from a different saver instance on the same database."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from langgraph.types import Command

from sc_core.graph import build_checkpointer, cleared, run_config
from sc_core.infra.db import Database
from sc_core.infra.settings import AppDbCfg
from tests.unit.graph.toy import FakeApprovalPorts, build_toy

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(fresh_postgres_dsn: str) -> AsyncIterator[Database]:
    database = Database(AppDbCfg(dsn=fresh_postgres_dsn))  # type: ignore[arg-type]
    await database.open()
    yield database
    await database.close()


async def test_interrupt_persists_and_resumes_in_a_new_process(db: Database) -> None:
    cfg = run_config("case_pg")
    ports_a = FakeApprovalPorts()
    graph_a = build_toy(ports_a, await build_checkpointer(db))
    out = await graph_a.ainvoke({"case_id": "case_pg", "inbound_text": "secreto"}, cfg)
    assert "__interrupt__" in out and len(ports_a.created) == 1

    # "another process": new ports, new saver, same database
    ports_b = FakeApprovalPorts()
    graph_b = build_toy(ports_b, await build_checkpointer(db))
    snapshot = await graph_b.aget_state(cfg)
    assert snapshot.next == ("send.await",)
    assert snapshot.values["inbound_text"] == "secreto"  # still needed while paused

    final = await graph_b.ainvoke(Command(resume={"approval_id": 101, "status": "approved"}), cfg)
    assert final["outcome"] == "sent" and ports_b.created == []
    persisted = (await graph_b.aget_state(cfg)).values
    assert cleared(persisted) and persisted["approvals"][0]["status"] == "approved"
