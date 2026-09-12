"""``python -m mail_sync.cli sync-once``: one sync from the host, report as JSON.

Uses the same wiring as the service (real Graph session, Odoo, app database,
Redis lock) so what it prints is exactly what the scheduled run would do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from injector import Injector

from mail_sync.module import MailSyncModule
from mail_sync.sync import SyncRunner
from sc_core.a2a.events import EventPublisher
from sc_core.infra.db import Database
from sc_core.infra.health import HealthRegistry
from sc_core.infra.logger import configure_logging
from sc_core.infra.migrate import apply_migrations
from sc_core.infra.module import CoreModule, DbModule, MailModule, OdooModule, RedisModule
from sc_core.infra.settings import Settings


def cmd_sync_once(settings: Settings) -> int:
    apply_migrations(str(settings.app_db.dsn), Path(settings.app_db.migrations_dir))
    injector = Injector(
        [
            CoreModule(settings, HealthRegistry()),
            DbModule(),
            RedisModule(),
            OdooModule(),
            MailModule(),
            MailSyncModule(),
        ]
    )

    async def run() -> int:
        db = injector.get(Database)
        await db.open()
        try:
            report = await injector.get(SyncRunner).run()
        finally:
            await injector.get(EventPublisher).aclose()
            await db.close()
        print(json.dumps(report.model_dump(), indent=2))
        return 0 if report.status in ("ok", "skipped_locked") else 1

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mail_sync")
    parser.add_argument("command", choices=["sync-once"])
    parser.parse_args(argv)
    settings = Settings(service_name="mail_sync")
    configure_logging(settings)
    return cmd_sync_once(settings)


if __name__ == "__main__":
    raise SystemExit(main())
