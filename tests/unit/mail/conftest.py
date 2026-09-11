"""Fixtures for Graph client tests: a fake token provider and a scripted transport."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from sc_core.mail.graph import GraphMailClient


class FakeTokens:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def access_token(self, *, force_refresh: bool = False) -> str:
        self.calls.append(force_refresh)
        return f"tok{len(self.calls)}" if force_refresh else "tok"

    def mailbox_prefix(self) -> str:
        return "/me"

    async def identity(self) -> str | None:
        return "bot@example.com"


@dataclass
class ScriptedGraph:
    """Answers requests in order and records them (method, url, headers, json)."""

    script: list[httpx.Response | Exception] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if not self.script:
                raise AssertionError(f"unexpected Graph request: {request.method} {request.url}")
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        return httpx.MockTransport(handler)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def json_of(self, index: int) -> Any:
        return json.loads(self.requests[index].content)


@dataclass
class Sleeps:
    delays: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def ok(payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


@pytest.fixture
def graph() -> ScriptedGraph:
    return ScriptedGraph()


@pytest.fixture
def tokens() -> FakeTokens:
    return FakeTokens()


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def client_factory(
    graph: ScriptedGraph, tokens: FakeTokens, sleeps: Sleeps
) -> Iterator[Callable[..., GraphMailClient]]:
    def make(**overrides: Any) -> GraphMailClient:
        return GraphMailClient(tokens, transport=graph.transport(), sleep=sleeps, **overrides)

    yield make
