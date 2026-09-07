"""Routes every service exposes."""

from sc_core.app.route.discovery import router as discovery_router
from sc_core.app.route.health import router as health_router

__all__ = ["discovery_router", "health_router"]
