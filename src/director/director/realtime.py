"""Where the director's live notifications come from.

Every change the Control Tower shows goes through the case store, so one
wrapper around it publishes the stream events: ``case_updated`` on any
change, plus ``approval_created`` / ``approval_resolved`` / ``run_finished``
derived from the case event that records them. Handlers and jobs need no
extra calls, and the API's own writes (a resolve, a note) publish the same
way.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from director.store import Case, CaseEvent, CaseEventKind, CaseKind, CaseStatus, CaseStore
from sc_core.app.realtime import Realtime

_EVENT_KINDS: dict[str, str] = {
    "approval_requested": "approval_created",
    "approval_resolved": "approval_resolved",
    "result": "run_finished",
}


class BroadcastingCaseStore:
    def __init__(self, inner: CaseStore, realtime: Realtime) -> None:
        self._inner = inner
        self._realtime = realtime

    async def attach_or_create(
        self,
        *,
        kind: CaseKind,
        po_name: str | None,
        partner_id: int | None = None,
        conversation_id: str | None = None,
        agent: str | None = None,
    ) -> tuple[Case, bool]:
        case, created = await self._inner.attach_or_create(
            kind=kind,
            po_name=po_name,
            partner_id=partner_id,
            conversation_id=conversation_id,
            agent=agent,
        )
        if created:
            await self._publish_case(case)
        return case, created

    async def get(self, case_id: str) -> Case | None:
        return await self._inner.get(case_id)

    async def update(self, case_id: str, **changes: Any) -> Case:
        case = await self._inner.update(case_id, **changes)
        await self._publish_case(case)
        return case

    async def add_event(self, case_id: str, kind: CaseEventKind, payload: dict[str, Any]) -> int:
        event_id = await self._inner.add_event(case_id, kind, payload)
        await self._realtime.publish("case_updated", {"case_id": case_id, "event": kind})
        if stream_kind := _EVENT_KINDS.get(kind):
            await self._realtime.publish(
                stream_kind,
                {
                    "case_id": case_id,
                    "approval_id": payload.get("approval_id"),
                    "run_id": payload.get("run_id"),
                    "status": payload.get("status"),
                },
            )
        return event_id

    async def events(self, case_id: str) -> list[CaseEvent]:
        return await self._inner.events(case_id)

    async def open_for_po(self, po_name: str) -> list[Case]:
        return await self._inner.open_for_po(po_name)

    async def find_by_thread(self, thread_id: str) -> Case | None:
        return await self._inner.find_by_thread(thread_id)

    async def rules_fired(self, po_name: str) -> list[str]:
        return await self._inner.rules_fired(po_name)

    async def earliest_created_at(self) -> datetime | None:
        return await self._inner.earliest_created_at()

    async def list(
        self,
        *,
        status: CaseStatus | None = None,
        po_name: str | None = None,
        kind: CaseKind | None = None,
        limit: int = 50,
    ) -> list[Case]:
        return await self._inner.list(status=status, po_name=po_name, kind=kind, limit=limit)

    async def _publish_case(self, case: Case) -> None:
        await self._realtime.publish(
            "case_updated",
            {"case_id": case.case_id, "po_name": case.po_name, "status": case.status},
        )
