"""Playbooks for the Control Tower: the plans, where every active run is, start and cancel."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.auth import Approver, Principal, Viewer
from director.playbooks import (
    PlaybookEngine,
    PlaybookPosition,
    PlaybookRun,
    PlaybookStore,
    StepRecord,
)
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError

router = APIRouter(prefix="/playbooks", tags=["playbooks"])


class StepView(StrictModel):
    id: str
    label: str
    kind: str
    when: str
    agent: str | None = None
    task: str | None = None
    wait_days: int | None = None
    until: str | None = None
    action: str | None = None
    active_runs: int = 0


class PlaybookView(StrictModel):
    name: str
    title: str
    description: str
    trigger: str
    steps: list[StepView]
    active_runs: int = 0


class RunView(StrictModel):
    run: PlaybookRun
    position: PlaybookPosition
    steps: list[StepRecord] = []


class StartRequest(StrictModel):
    playbook: str
    po_name: str = Field(min_length=1)
    partner_id: int | None = None


@router.get("", response_model=list[PlaybookView])
async def list_playbooks(
    _: Principal = Viewer,
    engine: PlaybookEngine = Injected(PlaybookEngine),
) -> list[PlaybookView]:
    counts = await engine.counts()
    out: list[PlaybookView] = []
    for name, playbook in engine.playbooks.items():
        per_step = counts.get(name, {})
        out.append(
            PlaybookView(
                name=name,
                title=playbook.title,
                description=playbook.description,
                trigger=playbook.trigger,
                steps=[
                    StepView(
                        id=s.id,
                        label=s.describe(),
                        kind=s.kind,
                        when=s.when,
                        agent=s.agent,
                        task=s.task,
                        wait_days=s.wait_days,
                        until=s.until,
                        action=s.action,
                        active_runs=per_step.get(s.id, 0),
                    )
                    for s in playbook.steps
                ],
                active_runs=sum(per_step.values()),
            )
        )
    return out


@router.get("/runs", response_model=list[RunView])
async def list_runs(
    active: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    _: Principal = Viewer,
    engine: PlaybookEngine = Injected(PlaybookEngine),
    store: PlaybookStore = Injected(PlaybookStore),  # type: ignore[type-abstract]
) -> list[RunView]:
    runs = await store.active() if active else await store.recent(limit=limit)
    return [RunView(run=r, position=engine.position(r)) for r in runs[:limit]]


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    run_id: int,
    _: Principal = Viewer,
    engine: PlaybookEngine = Injected(PlaybookEngine),
    store: PlaybookStore = Injected(PlaybookStore),  # type: ignore[type-abstract]
) -> RunView:
    run = await store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"playbook run {run_id} not found")
    return RunView(run=run, position=engine.position(run), steps=await store.steps(run_id))


@router.post("/runs", response_model=RunView, status_code=201)
async def start_run(
    body: StartRequest,
    principal: Principal = Approver,
    engine: PlaybookEngine = Injected(PlaybookEngine),
    store: PlaybookStore = Injected(PlaybookStore),  # type: ignore[type-abstract]
) -> RunView:
    """Start a playbook on an order by hand (the daily job starts the usual ones)."""
    if body.playbook not in engine.playbooks:
        raise HTTPException(status_code=404, detail=f"unknown playbook {body.playbook!r}")
    try:
        run = await engine.start(
            body.playbook,
            po_name=body.po_name,
            partner_id=body.partner_id,
            started_by=principal.email,
        )
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    logger.bind(playbook=body.playbook, po_name=body.po_name, by=principal.email).info(
        "playbook started from the Control Tower"
    )
    return RunView(run=run, position=engine.position(run), steps=await store.steps(run.id))


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(
    run_id: int,
    principal: Principal = Approver,
    engine: PlaybookEngine = Injected(PlaybookEngine),
    store: PlaybookStore = Injected(PlaybookStore),  # type: ignore[type-abstract]
) -> RunView:
    try:
        run = await engine.cancel(run_id, by=principal.email)
    except ScError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return RunView(run=run, position=engine.position(run), steps=await store.steps(run_id))


@router.post("/tick", response_model=dict[str, object])
async def tick(
    principal: Principal = Approver,
    engine: PlaybookEngine = Injected(PlaybookEngine),
) -> dict[str, object]:
    """Move every active run now (the hourly job does the same)."""
    result = await engine.tick()
    logger.bind(by=principal.email, moved=result["moved"]).info("playbooks ticked by hand")
    return {**result, "at": datetime.now().isoformat()}
