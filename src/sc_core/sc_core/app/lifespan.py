"""Application lifespan: logging first, then service-specific hooks."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from sc_core.infra.logger import configure_logging
from sc_core.infra.settings import Settings

Hook = Callable[[], Awaitable[None]]


def build_lifespan(
    settings: Settings,
    *,
    startup: Sequence[Hook] = (),
    shutdown: Sequence[Hook] = (),
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Return a lifespan context manager for ``FastAPI(lifespan=...)``.

    Startup hooks run in order; shutdown hooks run in reverse order so
    resources are released opposite to how they were acquired. A failing
    startup hook aborts the start, which is the safe behaviour for a service
    whose dependencies are misconfigured.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        logger.bind(environment=settings.environment).info("starting {}", settings.service_name)
        for hook in startup:
            await hook()
        try:
            yield
        finally:
            for hook in reversed(shutdown):
                try:
                    await hook()
                except Exception:  # noqa: BLE001 - keep shutting down the rest
                    logger.opt(exception=True).error("shutdown hook failed")
            logger.info("stopped {}", settings.service_name)

    return lifespan
