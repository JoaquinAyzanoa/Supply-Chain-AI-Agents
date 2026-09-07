"""FastAPI application factory.

Every service builds its app with :func:`create_application` so that all of
them share the same middlewares, system routes, error format, dependency
injection and lifespan. A service adds its own routers, health checks,
injector modules and startup hooks; nothing else differs between them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from fastapi import APIRouter, FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from sc_core.app.handlers import UnhandledErrorMiddleware, install_exception_handlers
from sc_core.app.lifespan import Hook, build_lifespan
from sc_core.app.middleware import (
    RequestIdMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from sc_core.app.route import discovery_router, health_router
from sc_core.infra.health import HealthCheck, HealthRegistry
from sc_core.infra.module import CoreModule
from sc_core.infra.settings import Settings

# (name, check, critical). Timeout uses the registry default.
HealthCheckSpec = tuple[str, HealthCheck, bool]


def create_application(
    settings: Settings,
    *,
    version: str = "0.1.0",
    routers: Iterable[APIRouter] = (),
    health_checks: Iterable[HealthCheckSpec] = (),
    modules: Iterable[Module] = (),
    startup: Sequence[Hook] = (),
    shutdown: Sequence[Hook] = (),
) -> FastAPI:
    """Build a configured FastAPI app for ``settings.service_name``."""
    health = HealthRegistry()
    for name, check, critical in health_checks:
        health.register(name, check, critical=critical)

    injector = Injector([CoreModule(settings, health), *modules])

    app = FastAPI(
        title=settings.service_name,
        version=version,
        lifespan=build_lifespan(settings, startup=startup, shutdown=shutdown),
        docs_url="/docs" if settings.is_dev else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.health = health
    app.state.injector = injector
    attach_injector(app, injector)

    # Middleware order: the last added runs first (outermost). From the
    # outside in: request id + context, security headers, size limit, crash
    # handler, then the app. That way a 500 or a 413 still carries the
    # request id and the security headers.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.http.max_request_bytes)
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=settings.http.enable_hsts)
    app.add_middleware(RequestIdMiddleware, service_name=settings.service_name)

    install_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(discovery_router)
    for router in routers:
        app.include_router(router)
    return app
