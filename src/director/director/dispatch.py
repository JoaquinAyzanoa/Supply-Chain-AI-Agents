"""Interim dispatch: the routing table decides, the agent is called over A2A.

The tasks the router produces are sent with the event's case id so the
agent's spans nest under the producer's trace, and the agent's reply is
stored on the inbox row. The Microsoft Agent Framework workflow (P6-S3)
replaces this class; the routing table stays.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from director.router import Dispatch, UnroutableEvent, route
from sc_core.a2a.client import AgentCaller
from sc_core.infra import tracing
from sc_core.infra.db import Database
from sc_core.schema.events import BaseEvent
from sc_core.shared.errors import ScError


@runtime_checkable
class EventResults(Protocol):
    async def record(self, event_id: str, result: dict[str, Any]) -> None: ...


class PostgresEventResults:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, event_id: str, result: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE event_inbox SET handled_at = now(), result = %s::jsonb WHERE event_id = %s",
            (json.dumps(result, default=str), event_id),
        )


class MemoryEventResults:
    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}

    async def record(self, event_id: str, result: dict[str, Any]) -> None:
        self.results[event_id] = result


def task_for(event: BaseEvent) -> Dispatch | None:
    """The first agent task the router produces for ``event``, if any."""
    try:
        decided = route(event)
    except UnroutableEvent:
        return None
    return decided.dispatches[0] if decided.dispatches else None


class Dispatcher:
    def __init__(self, supplier_comms: AgentCaller, results: EventResults) -> None:
        self._supplier_comms = supplier_comms
        self._results = results

    async def dispatch(self, event: BaseEvent) -> dict[str, Any] | None:
        dispatch = task_for(event)
        if dispatch is None:
            return None
        task = dispatch.task
        log = logger.bind(event_id=event.event_id, case_id=event.case_id, kind=task.kind)
        try:
            if event.trace_id:
                with tracing.continue_trace(
                    event.trace_id, "director.dispatch", case_id=event.case_id
                ):
                    reply = await self._supplier_comms.send(
                        task.model_dump_json(), case_id=event.case_id
                    )
            else:
                with tracing.start_case(event.case_id, "director.dispatch"):
                    reply = await self._supplier_comms.send(
                        task.model_dump_json(), case_id=event.case_id
                    )
        except ScError as exc:
            log.opt(exception=True).error("agent call failed: {}", exc.message)
            result = {"agent": "supplier_comms", "task": task.kind, "error": exc.to_dict()}
            await self._results.record(event.event_id, result)
            return result
        result = {
            "agent": "supplier_comms",
            "task": task.kind,
            "status": reply.status,
            "reply": _json_or_text(reply.text),
        }
        await self._results.record(event.event_id, result)
        log.bind(status=reply.status).info("event dispatched")
        return result


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return text
