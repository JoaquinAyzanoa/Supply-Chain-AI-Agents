"""Runtime settings: the environment baseline, the cached reader and the policy merge."""

from __future__ import annotations

import json
from typing import Any

from director.policies import FollowUpPolicy
from sc_core.infra.runtime_settings import MemoryRuntimeSettingsReader, RuntimeSettingsReader
from sc_core.infra.settings import Settings
from sc_core.schema.runtime_settings import RuntimeSettings


class FakeDb:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls = 0
        self.fail = False

    async def fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self.calls += 1
        if self.fail:
            raise ConnectionError("db down")
        return self.row


def test_version_zero_mirrors_the_environment() -> None:
    settings = Settings(
        _env_file=None,
        service_name="t",
        environment="test",
        director={"rfq_no_reply_days": [2, 5], "max_actions_per_run": 3},
        supplier_comms={"auto_send_partner_ids": [7]},
        llm={"model": {"SUPPLIER_COMMS": "gpt-5.4"}},
    )
    baseline = RuntimeSettings.from_settings(settings)
    assert baseline.rfq_no_reply_days == [2, 5] and baseline.max_actions_per_run == 3
    assert baseline.auto_send_partner_ids == [7]
    assert baseline.model_for("supplier_comms") == "gpt-5.4" and baseline.model_for("x") is None
    assert baseline.planning_service_level is None  # keep the ABC class defaults


async def test_reader_caches_for_the_ttl_and_invalidates() -> None:
    db = FakeDb({"version": 3, "settings": json.dumps({"rfq_no_reply_days": [1, 2]})})
    reader = RuntimeSettingsReader(db, ttl_seconds=60)  # type: ignore[arg-type]
    first = await reader.current()
    assert first.rfq_no_reply_days == [1, 2] and reader.version == 3
    db.row = {"version": 4, "settings": {"rfq_no_reply_days": [9]}}
    assert (await reader.current()).rfq_no_reply_days == [1, 2] and db.calls == 1  # cached
    reader.invalidate()
    assert (await reader.current()).rfq_no_reply_days == [9] and reader.version == 4


async def test_reader_falls_back_to_defaults_and_keeps_the_last_value_on_errors() -> None:
    defaults = RuntimeSettings(max_actions_per_run=5)
    empty = RuntimeSettingsReader(FakeDb(None), defaults=defaults)  # type: ignore[arg-type]
    assert (await empty.current()).max_actions_per_run == 5 and empty.version == 0
    assert (await RuntimeSettingsReader(None, defaults=defaults).current()) is defaults

    db = FakeDb({"version": 1, "settings": {"max_actions_per_run": 8}})
    reader = RuntimeSettingsReader(db, defaults=defaults, ttl_seconds=0)  # type: ignore[arg-type]
    assert (await reader.current()).max_actions_per_run == 8
    db.fail = True
    assert (await reader.current()).max_actions_per_run == 8  # stale beats failed

    bad = FakeDb({"version": 2, "settings": {"max_actions_per_run": "many"}})
    assert (await RuntimeSettingsReader(bad, defaults=defaults).current()) is defaults  # type: ignore[arg-type]


def test_policy_takes_the_runtime_values() -> None:
    policy = FollowUpPolicy(rfq_no_reply_days=[3, 7], approval_expire_days=7)
    merged = policy.with_runtime(
        RuntimeSettings(rfq_no_reply_days=[2], approval_expire_days=10, po_late_days=[2, 6])
    )
    assert merged.rfq_no_reply_days == [2] and merged.approval_expire_days == 10
    assert merged.po_late_days == [2, 6] and policy.rfq_no_reply_days == [3, 7]


async def test_memory_reader_counts_reads() -> None:
    reader = MemoryRuntimeSettingsReader(RuntimeSettings(auto_send_partner_ids=[1]))
    assert (await reader.current()).auto_send_partner_ids == [1]
    reader.set(RuntimeSettings(auto_send_partner_ids=[2]))
    assert (await reader.current()).auto_send_partner_ids == [2] and reader.reads == 2


def test_browser_urls_default_to_the_service_urls() -> None:
    inside = Settings(
        _env_file=None,
        service_name="t",
        environment="test",
        odoo={"url": "http://odoo:8069", "public_url": "http://localhost:8069/"},
        langfuse={"host": "http://langfuse-web:3000", "public_url": "http://localhost:3000"},
    )
    assert inside.odoo.browser_url == "http://localhost:8069"
    assert inside.langfuse.browser_url == "http://localhost:3000"
    plain = Settings(_env_file=None, service_name="t", environment="test")
    assert plain.odoo.browser_url == plain.odoo.url.rstrip("/")
    assert plain.langfuse.browser_url == plain.langfuse.host
