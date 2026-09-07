"""Discovery endpoint: what this service is and which routes it exposes.

Routes are read from the generated OpenAPI document rather than from
``app.routes``. Recent FastAPI versions keep included routers as opaque
nested objects in ``app.routes``, while the OpenAPI document is a stable,
public representation that already has prefixes applied.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi_injector import Injected

from sc_core import __meta__
from sc_core.infra.settings import Settings
from sc_core.schema.system import DiscoveryResponse, RouteInfo

router = APIRouter(tags=["system"])

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def routes_from_openapi(schema: dict[str, Any]) -> list[RouteInfo]:
    routes: list[RouteInfo] = []
    for path, operations in schema.get("paths", {}).items():
        methods = sorted(m.upper() for m in operations if m in _HTTP_METHODS)
        if not methods:
            continue
        first = operations[next(m for m in operations if m in _HTTP_METHODS)]
        routes.append(RouteInfo(path=path, methods=methods, name=first.get("operationId", "")))
    return sorted(routes, key=lambda r: r.path)


@router.get("/discovery", response_model=DiscoveryResponse)
async def discovery(request: Request, settings: Settings = Injected(Settings)) -> DiscoveryResponse:
    return DiscoveryResponse(
        service=settings.service_name,
        version=request.app.version,
        core_version=__meta__.__version__,
        environment=settings.environment,
        routes=routes_from_openapi(request.app.openapi()),
    )
