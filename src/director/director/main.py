"""ASGI entrypoint for the director service.

Phase 0, story P0-S1: a minimal application that proves the package imports
and runs. Story P0-S3 replaces the bare ``FastAPI()`` with
``sc_core.app.create_application`` so that health, discovery and middleware
are shared with every other service.
"""

from fastapi import FastAPI

import sc_core

app = FastAPI(title="director")


@app.get("/")
async def root() -> dict[str, str]:
    """Smoke endpoint used by the phase 0 acceptance checks."""
    return {"service": "director", "sc_core": sc_core.__name__}
