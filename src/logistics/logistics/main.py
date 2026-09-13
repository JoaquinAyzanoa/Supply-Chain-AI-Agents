"""ASGI entrypoint for the logistics agent.

Routes: the shared system surface, ``POST /approvals/callback`` and the A2A
agent (card at ``/.well-known/agent-card.json``, JSON-RPC at ``/a2a`` behind
the bearer token).
"""

from __future__ import annotations

from fastapi import FastAPI
from loguru import logger

from logistics import __version__
from logistics.handler import LogisticsHandler, agent_spec
from logistics.routers import approvals
from logistics.service import AgentProvider, module_list
from sc_core.a2a import mount
from sc_core.app import create_application
from sc_core.infra.db import Database
from sc_core.infra.settings import Settings
from sc_core.odoo.client import OdooClient

settings = Settings(service_name="logistics")


def build_app() -> FastAPI:
    application = create_application(
        settings,
        version=__version__,
        routers=[approvals.router],
        modules=module_list(),
        startup=[_startup],
        shutdown=[_shutdown],
    )
    if settings.a2a_token:
        mount(
            application,
            LogisticsHandler(application.state.injector.get(AgentProvider)),
            agent_spec(settings.logistics.public_url),
            token=settings.a2a_token,
        )
    else:
        logger.warning(
            "no A2A token (SC__A2A__TOKEN / SC__EVENTS__SIGNING_SECRET); A2A not mounted"
        )
    return application


async def _startup() -> None:
    injector = app.state.injector
    await injector.get(Database).open()
    if settings.odoo.configured:
        await injector.get(AgentProvider).get()
    else:
        logger.warning("SC__ODOO__API_KEY not set; the agent is not built until configured")


async def _shutdown() -> None:
    injector = app.state.injector
    if settings.odoo.configured:
        await injector.get(OdooClient).aclose()
    await injector.get(Database).close()


if not settings.events.configured:
    logger.warning("SC__EVENTS__SIGNING_SECRET not set; approval callbacks will be rejected")

app = build_app()
