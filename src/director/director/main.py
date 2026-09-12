"""ASGI entrypoint for the director service.

Phase 4: signed events from mail_sync and the scheduler land in the event
inbox. Phase 6 adds the orchestration (routing to agents over A2A) on top
of the same routes; phase 8 adds the Control Tower API.
"""

from __future__ import annotations

from fastapi import FastAPI
from injector import Binder, Module, singleton
from loguru import logger

from director import __version__
from director.inbox import EventInbox, PostgresEventInbox
from director.routers import events
from sc_core.app import create_application
from sc_core.infra.db import Database
from sc_core.infra.module import DbModule, OdooModule
from sc_core.infra.settings import Settings
from sc_core.odoo.client import OdooClient

settings = Settings(service_name="director")


class InboxModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(EventInbox, to=PostgresEventInbox, scope=singleton)  # type: ignore[type-abstract]


def build_app() -> FastAPI:
    application = create_application(
        settings,
        version=__version__,
        routers=[events.router],
        modules=[DbModule(), OdooModule(), InboxModule()],
        startup=[_open_db, _connect_odoo],
        shutdown=[_close_odoo, _close_db],
    )
    return application


async def _open_db() -> None:
    await app.state.injector.get(Database).open()


async def _close_db() -> None:
    await app.state.injector.get(Database).close()


async def _connect_odoo() -> None:
    """Instantiate the Odoo client at startup so its readiness check is registered.

    Without an API key (CI, a fresh checkout) the service still starts and
    simply does not report Odoo; the first real use would fail loudly.
    """
    if not settings.odoo.configured:
        logger.warning("SC__ODOO__API_KEY not set; odoo readiness check not registered")
        return
    app.state.injector.get(OdooClient)


async def _close_odoo() -> None:
    if settings.odoo.configured:
        await app.state.injector.get(OdooClient).aclose()


if not settings.events.configured:
    logger.warning("SC__EVENTS__SIGNING_SECRET not set; /events will reject every request")

app = build_app()
