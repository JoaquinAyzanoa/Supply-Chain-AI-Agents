"""ASGI entrypoint for the director service.

The orchestrator's own routers (events, Odoo webhooks, jobs, the Control
Tower API) are added in phases 4, 6 and 8. Until then the app exposes only
the shared system routes.
"""

from director import __version__
from sc_core.app import create_application
from sc_core.infra.settings import Settings

settings = Settings(service_name="director")
app = create_application(settings, version=__version__)
