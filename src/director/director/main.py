"""ASGI entrypoint for the director service.

Phase 4: signed events from mail_sync and the scheduler land in the event
inbox. Phase 5: mail events are dispatched to the supplier communications
agent over A2A. Phase 6 adds the orchestration workflow; phase 8 the
Control Tower API.
"""

from __future__ import annotations

from fastapi import FastAPI
from injector import Binder, Module, provider, singleton
from loguru import logger

from director import __version__
from director.dispatch import Dispatcher, EventResults, PostgresEventResults
from director.inbox import EventInbox, PostgresEventInbox
from director.routers import events
from sc_core.a2a.client import A2AClient, AgentCaller
from sc_core.app import create_application
from sc_core.infra.db import Database
from sc_core.infra.module import DbModule, OdooModule
from sc_core.infra.settings import Settings
from sc_core.odoo.client import OdooClient

settings = Settings(service_name="director")


class InboxModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(EventInbox, to=PostgresEventInbox, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(EventResults, to=PostgresEventResults, scope=singleton)  # type: ignore[type-abstract]

    @provider
    @singleton
    def provide_supplier_comms(self, settings: Settings) -> AgentCaller:  # type: ignore[type-abstract]
        return A2AClient(
            settings.a2a.supplier_comms_url,
            token=settings.a2a_token,
            timeout_seconds=settings.a2a.timeout_seconds,
        )

    @provider
    @singleton
    def provide_dispatcher(
        self,
        supplier_comms: AgentCaller,  # type: ignore[type-abstract]
        results: EventResults,  # type: ignore[type-abstract]
    ) -> Dispatcher:
        return Dispatcher(supplier_comms, results)


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
