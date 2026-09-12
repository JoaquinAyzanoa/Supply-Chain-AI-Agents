"""ASGI entrypoint for mail_sync.

``POST /jobs/sync`` runs one sync under the Redis lock and returns the
report. The scheduler calls it every 30 minutes with a signed body; ``just
sync-now`` does the same by hand. Everything else is the shared system
surface (health, discovery).
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi_injector import Injected

from mail_sync import __version__
from mail_sync.module import MailSyncModule
from mail_sync.sync import SyncReport, SyncRunner
from sc_core.a2a.events import EventPublisher, SignedBody
from sc_core.app import create_application
from sc_core.infra.db import Database
from sc_core.infra.module import DbModule, MailModule, OdooModule, RedisModule
from sc_core.infra.settings import Settings

settings = Settings(service_name="mail_sync")

router = APIRouter(tags=["jobs"])


@router.post("/jobs/sync", response_model=SyncReport)
async def run_sync(
    _body: bytes = SignedBody, runner: SyncRunner = Injected(SyncRunner)
) -> SyncReport:
    return await runner.run()


def build_app() -> FastAPI:
    return create_application(
        settings,
        version=__version__,
        routers=[router],
        modules=[
            DbModule(),
            RedisModule(),
            OdooModule(),
            MailModule(critical=False),
            MailSyncModule(),
        ],
        startup=[_startup],
        shutdown=[_shutdown],
    )


async def _startup() -> None:
    injector = app.state.injector
    await injector.get(Database).open()
    # Building the runner instantiates the Odoo and Graph clients, which
    # registers their readiness checks.
    injector.get(SyncRunner)


async def _shutdown() -> None:
    injector = app.state.injector
    await injector.get(EventPublisher).aclose()
    await injector.get(Database).close()


app = build_app()
