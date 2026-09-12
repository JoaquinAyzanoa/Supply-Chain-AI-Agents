"""Serve a built single-page app next to the API.

``mount_spa(app, dist)`` serves the Vite bundle: hashed assets under
``/assets`` with long caching, every other file in ``dist`` as is, and
``index.html`` for any path that is not a file (the SPA router owns those).
API, events and system routes are registered before, so they keep winning.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

RESERVED_PREFIXES = ("api/", "events", "health", "discovery", "docs", "openapi.json", "a2a")


def mount_spa(app: FastAPI, dist: Path) -> bool:
    """Mount ``dist`` under ``/``. Returns False (and logs) when there is no bundle."""
    index = dist / "index.html"
    if not index.is_file():
        logger.info("no frontend bundle at {}; the API runs without the UI", dist)
        return False
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(RESERVED_PREFIXES):
            raise HTTPException(status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    logger.info("serving the Control Tower from {}", dist)
    return True
