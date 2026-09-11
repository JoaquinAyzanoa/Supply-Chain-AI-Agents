"""ASGI entrypoint for the director service.

The orchestrator's own routers (events, Odoo webhooks, jobs, the Control
Tower API) are added in phases 4, 6 and 8. Until then the app exposes the
shared system routes, with Odoo wired into readiness.
"""

from fastapi import FastAPI
from loguru import logger

from director import __version__
from sc_core.app import create_application
from sc_core.infra.module import OdooModule
from sc_core.infra.settings import Settings
from sc_core.odoo.client import OdooClient

settings = Settings(service_name="director")


def build_app() -> FastAPI:
    application = create_application(
        settings,
        version=__version__,
        modules=[OdooModule()],
        startup=[_connect_odoo],
        shutdown=[_close_odoo],
    )
    return application


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


app = build_app()
