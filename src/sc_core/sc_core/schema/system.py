"""Response models for the system routes shared by every service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class LiveResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str


class RouteInfo(BaseModel):
    path: str
    methods: list[str]
    name: str


class DiscoveryResponse(BaseModel):
    service: str
    version: str
    core_version: str
    environment: str
    routes: list[RouteInfo]


class ErrorResponse(BaseModel):
    """Body returned for every handled error, shaped like ``ScError.to_dict``."""

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = {}
    request_id: str | None = None
