"""Job table: crons parse, env overrides apply, invalid crons fail at startup."""

from __future__ import annotations

import pytest

from sc_core.infra.settings import Settings
from sc_core.shared.errors import ConfigurationError
from scheduler.jobs import Job, job_table


def test_default_table_targets_and_crons() -> None:
    jobs = {j.id: j for j in job_table(Settings(_env_file=None))}
    assert set(jobs) == {"mail_sync", "po_followups", "inventory_planning", "supplier_performance"}
    assert jobs["mail_sync"].cron == "*/5 * * * *"
    assert jobs["mail_sync"].target == "http://localhost:8011/jobs/sync"
    assert jobs["po_followups"].target == "http://localhost:8010/jobs/po-followups"
    for job in jobs.values():
        assert job.trigger("America/Lima") is not None and job.overlap == "skip"


def test_env_override_changes_cron_and_urls(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SC__SCHEDULER__MAIL_SYNC_CRON", "*/5 * * * *")
    clean_env.setenv("SC__MAIL_SYNC__URL", "http://mail_sync:8000/")
    clean_env.setenv("SC__EVENTS__DIRECTOR_URL", "http://director:8000")
    jobs = {j.id: j for j in job_table(Settings(_env_file=None))}
    assert jobs["mail_sync"].cron == "*/5 * * * *"
    assert jobs["mail_sync"].target == "http://mail_sync:8000/jobs/sync"
    assert jobs["inventory_planning"].target == "http://director:8000/jobs/inventory-planning"


def test_invalid_cron_is_a_configuration_error(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SC__SCHEDULER__PO_FOLLOWUPS_CRON", "every day at nine")
    with pytest.raises(ConfigurationError, match="po_followups"):
        job_table(Settings(_env_file=None))
    with pytest.raises(ConfigurationError):
        Job(id="x", cron="* * *", target="http://t").trigger("UTC")
