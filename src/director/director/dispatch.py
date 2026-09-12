"""Phase 5 stub routing: mail events go to the supplier communications agent.

``InboundMailLinked`` becomes a ``handle_inbound`` task and
``InboundMailUnlinked`` a ``resolve_unlinked`` task, sent over A2A with the
event's case id so the agent's spans nest under the mail_sync trace. The
agent's reply is stored on the inbox row. Phase 6 replaces this with the
routing table and the Microsoft Agent Framework workflow; the call shape
stays.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from sc_core.a2a.client import AgentCaller
from sc_core.infra import tracing
from sc_core.infra.db import Database
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.events import BaseEvent, InboundMailLinked, InboundMailUnlinked
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


def task_for(event: BaseEvent) -> SupplierCommsTask | None:
    if isinstance(event, InboundMailLinked):
        return SupplierCommsTask(
            kind="handle_inbound",
            case_id=event.case_id,
            po_name=event.po_name,
            graph_message_id=event.graph_message_id,
        )
    if isinstance(event, InboundMailUnlinked):
        return SupplierCommsTask(
            kind="resolve_unlinked",
            case_id=event.case_id,
            graph_message_id=event.graph_message_id,
            candidate_po_names=list(event.open_po_names),
        )
    return None


class Dispatcher:
    def __init__(self, supplier_comms: AgentCaller, results: EventResults) -> None:
        self._supplier_comms = supplier_comms
        self._results = results

    async def dispatch(self, event: BaseEvent) -> dict[str, Any] | None:
        task = task_for(event)
        if task is None:
            return None
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
