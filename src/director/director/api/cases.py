"""Cases for the Control Tower: the list and one case's timeline.

A case is one PO-centred story. Its detail carries the case events in
order (event received, rule fired, task sent, result, approval requested
and resolved, escalation, notes) and the agent runs that worked on it,
each with its model, tokens, cost and trace link. Email bodies never
appear here: only subjects' tokens, directions and Outlook links.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from pydantic import Field

from director.api.auth import Principal, Viewer
from director.api.runs import AgentRunView, RunsGateway, run_view
from director.store import Case, CaseEvent, CaseKind, CaseStatus, CaseStore, parse_case_code
from sc_core.infra import tracing
from sc_core.schema.base import StrictModel

router = APIRouter(prefix="/cases", tags=["cases"])


class CaseView(StrictModel):
    case_id: str
    code: str = Field(description="what people read: C00012")
    kind: CaseKind
    status: CaseStatus
    po_name: str | None = None
    partner_id: int | None = None
    conversation_id: str | None = None
    agent: str | None = None
    summary: str | None = None
    created_at: datetime
    updated_at: datetime
    next_action_at: datetime | None = None
    trace_url: str | None = None


class CaseEventView(StrictModel):
    id: int
    at: datetime
    kind: str
    payload: dict[str, Any]


class CaseDetail(StrictModel):
    case: CaseView
    events: list[CaseEventView]
    runs: list[AgentRunView]


def case_view(case: Case) -> CaseView:
    return CaseView(
        **case.model_dump(exclude={"trace_id", "number"}),
        code=case.code,
        trace_url=tracing.trace_url(case.trace_id),
    )


async def load_case(cases: CaseStore, ref: str) -> Case:
    """A case by id or by its code (``C00012``); 404 when neither matches."""
    number = parse_case_code(ref)
    case = await cases.by_number(number) if number is not None else await cases.get(ref)
    if case is None:
        raise HTTPException(status_code=404, detail=f"case {ref} not found")
    return case


def _event_view(event: CaseEvent) -> CaseEventView:
    return CaseEventView(id=event.id, at=event.at, kind=event.kind, payload=event.payload)


@router.get("", response_model=list[CaseView])
async def list_cases(
    status: CaseStatus | None = Query(default=None),
    po: str | None = Query(default=None),
    kind: CaseKind | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    _: Principal = Viewer,
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
) -> list[CaseView]:
    rows = await cases.list(status=status, po_name=po, kind=kind, limit=limit)
    return [case_view(c) for c in rows]


@router.get("/{case_id}", response_model=CaseDetail)
async def get_case(
    case_id: str,
    _: Principal = Viewer,
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    runs: RunsGateway = Injected(RunsGateway),  # type: ignore[type-abstract]
) -> CaseDetail:
    case = await load_case(cases, case_id)
    case_id = case.case_id
    events = await cases.events(case_id)
    # The agents log their runs under the thread ids the director sent tasks on.
    thread_ids = {
        case_id,
        *(str(e.payload["thread_id"]) for e in events if e.payload.get("thread_id")),
    }
    agent_runs = await runs.for_cases(sorted(thread_ids))
    return CaseDetail(
        case=case_view(case),
        events=[_event_view(e) for e in events],
        runs=[run_view(r) for r in agent_runs],
    )
