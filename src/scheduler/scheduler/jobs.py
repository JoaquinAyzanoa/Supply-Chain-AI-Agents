"""The job table.

Jobs are declared in code and their crons come from settings, so nothing
about *what* runs needs to be persisted: after a restart the same table is
rebuilt and only run history (``scheduler_runs``) is read back. Each job is
an HTTP target inside the compose network, called with a signed
``ScheduledTick`` body.
"""

from __future__ import annotations

from typing import Literal

from apscheduler.triggers.cron import CronTrigger

from sc_core.infra.settings import Settings
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ConfigurationError


class Job(StrictModel):
    id: str
    cron: str
    target: str
    timeout_seconds: float = 600.0
    overlap: Literal["skip"] = "skip"  # a firing while the previous run is active is skipped

    def trigger(self, timezone: str) -> CronTrigger:
        try:
            return CronTrigger.from_crontab(self.cron, timezone=timezone)
        except ValueError as exc:
            raise ConfigurationError(
                f"job {self.id!r} has an invalid cron {self.cron!r}: {exc}"
            ) from exc


def job_table(settings: Settings) -> list[Job]:
    s = settings.scheduler
    director = settings.events.director_url.rstrip("/")
    mail_sync = settings.mail_sync.url.rstrip("/")
    jobs = [
        Job(
            id="mail_sync",
            cron=s.mail_sync_cron,
            target=f"{mail_sync}/jobs/sync",
            timeout_seconds=s.dispatch_timeout_seconds,
        ),
        Job(
            id="po_followups",
            cron=s.po_followups_cron,
            target=f"{director}/jobs/po-followups",
            timeout_seconds=900,
        ),
        Job(
            id="inventory_planning",
            cron=s.inventory_planning_cron,
            target=f"{director}/jobs/inventory-planning",
            timeout_seconds=3600,
        ),
        Job(
            id="supplier_performance",
            cron=s.supplier_performance_cron,
            target=f"{director}/jobs/supplier-performance",
            timeout_seconds=1800,
        ),
    ]
    for job in jobs:  # fail at startup, not at the first firing
        job.trigger(settings.timezone)
    return jobs
