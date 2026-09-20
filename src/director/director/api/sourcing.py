"""Sourcing for the Control Tower: the rounds, and starting a round, a comparison or
a counter-offer by hand. The agent does the work; every commitment is an approval."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field, model_validator

from director.api.auth import Approver, Principal, Viewer
from director.desk.sourcing import SourcingDispatcher, SourcingSource
from sc_core.schema.a2a import SourcingTask
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.idempotency import new_id

router = APIRouter(prefix="/sourcing", tags=["sourcing"])


class StartRoundRequest(StrictModel):
    po_name: str | None = Field(default=None, description="requote this order's lines")
    product_id: int | None = None
    qty: float | None = Field(default=None, gt=0)
    partner_ids: list[int] = Field(default_factory=list, description="invite these as well")
    max_suppliers: int | None = Field(default=None, ge=1, le=10)
    deadline_days: int | None = Field(default=None, ge=1, le=60)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _one_way(self) -> StartRoundRequest:
        if not self.po_name and not (self.product_id and self.qty):
            raise ValueError("give po_name, or product_id and qty")
        return self


class NegotiateRequest(StrictModel):
    po_name: str = Field(min_length=1)
    product_id: int | None = None
    target_price: float | None = Field(default=None, gt=0)


class DispatchResponse(StrictModel):
    case_id: str
    case_code: str | None = None
    thread_id: str
    status: str
    summary: str
    approval_id: int | None = None


@router.get("/rounds", response_model=list[dict[str, Any]])
async def rounds(
    status: str | None = Query(default=None),
    partner_id: int | None = Query(default=None),
    _: Principal = Viewer,
    source: SourcingSource = Injected(SourcingSource),  # type: ignore[type-abstract]
) -> list[dict[str, Any]]:
    try:
        return await source.rounds(status=status, partner_id=partner_id)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.get("/rounds/{round_id}", response_model=dict[str, Any])
async def get_round(
    round_id: int,
    _: Principal = Viewer,
    source: SourcingSource = Injected(SourcingSource),  # type: ignore[type-abstract]
) -> dict[str, Any]:
    try:
        found = await source.round(round_id)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if found is None:
        raise HTTPException(status_code=404, detail=f"round {round_id} not found")
    return found


@router.get("/negotiations", response_model=list[dict[str, Any]])
async def negotiations(
    po_name: str = Query(...),
    _: Principal = Viewer,
    source: SourcingSource = Injected(SourcingSource),  # type: ignore[type-abstract]
) -> list[dict[str, Any]]:
    try:
        return await source.negotiations(po_name)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.post("/rounds", response_model=DispatchResponse, status_code=202)
async def start_round(
    body: StartRoundRequest,
    principal: Principal = Approver,
    dispatcher: SourcingDispatcher = Injected(SourcingDispatcher),
) -> DispatchResponse:
    """Start a quote round by hand; the invitations go through the supplier agent."""
    task = SourcingTask(
        kind="quote_round",
        case_id=new_id("round"),
        po_name=body.po_name,
        product_id=body.product_id,
        qty=body.qty,
        partner_ids=body.partner_ids,
        max_suppliers=body.max_suppliers,
        deadline_days=body.deadline_days,
        notes=body.notes,
        reason=f"started from the Control Tower by {principal.email}",
    )
    logger.bind(po_name=body.po_name, product_id=body.product_id, by=principal.email).info(
        "quote round requested"
    )
    return DispatchResponse(**await dispatcher.run(task, requested_by=principal.email))


@router.post("/rounds/{round_id}/compare", response_model=DispatchResponse, status_code=202)
async def compare_round(
    round_id: int,
    principal: Principal = Approver,
    source: SourcingSource = Injected(SourcingSource),  # type: ignore[type-abstract]
    dispatcher: SourcingDispatcher = Injected(SourcingDispatcher),
) -> DispatchResponse:
    """Compare the quotes now instead of waiting for the deadline."""
    try:
        found = await source.round(round_id)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    if found is None:
        raise HTTPException(status_code=404, detail=f"round {round_id} not found")
    if found.get("status") not in ("open", "comparing", "rejected"):
        raise HTTPException(
            status_code=409, detail=f"round {round_id} is {found.get('status')}; nothing to compare"
        )
    task = SourcingTask(
        kind="compare_quotes",
        case_id=f"{found['case_id']}_cmp_{new_id('c')}",  # its own thread
        round_id=round_id,
        po_name=found.get("source_po_name"),
        reason=f"compared from the Control Tower by {principal.email}",
    )
    return DispatchResponse(**await dispatcher.run(task, requested_by=principal.email))


@router.post("/negotiate", response_model=DispatchResponse, status_code=202)
async def negotiate(
    body: NegotiateRequest,
    principal: Principal = Approver,
    dispatcher: SourcingDispatcher = Injected(SourcingDispatcher),
) -> DispatchResponse:
    """Ask the sourcing agent for a counter-offer on a quoted line; a person approves it."""
    task = SourcingTask(
        kind="counter_offer",
        case_id=new_id("offer"),
        po_name=body.po_name,
        product_id=body.product_id,
        target_price=body.target_price,
        reason=f"requested from the Control Tower by {principal.email}",
    )
    return DispatchResponse(**await dispatcher.run(task, requested_by=principal.email))
