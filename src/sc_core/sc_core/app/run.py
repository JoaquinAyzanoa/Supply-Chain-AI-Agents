"""Generic process entrypoint used by every container.

``python -m sc_core.app.run`` reads ``SC__SERVICE_NAME`` (for example
``director``), imports ``<service>.main:app`` and serves it with uvicorn. The
Dockerfile uses this so one image recipe works for every member; a service's
``python -m <service>`` calls :func:`serve` with its own name.
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from sc_core.infra.settings import get_settings


def use_selector_loop_on_windows() -> None:
    """psycopg's async connection cannot run on the Windows Proactor loop.

    Linux containers are unaffected; this only matters for developers
    running a service directly on Windows.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def serve(service_name: str | None = None, *, reload: bool = False) -> None:
    use_selector_loop_on_windows()
    settings = get_settings()
    name = service_name or settings.service_name
    if name == "unnamed":
        raise SystemExit("set SC__SERVICE_NAME (e.g. director) or pass a service name")
    uvicorn.run(
        f"{name}.main:app",
        host=settings.http.host,
        port=settings.http.port,
        reload=reload,
        log_config=None,  # our loguru sink intercepts uvicorn's loggers
        access_log=settings.http.access_log,
    )


if __name__ == "__main__":
    serve()
