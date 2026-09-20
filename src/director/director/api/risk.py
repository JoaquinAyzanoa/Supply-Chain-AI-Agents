"""The risk radar for the Control Tower, read from the planner, with one-click responses.

``POST /api/risk/{product_id}/act`` starts the right sourcing move: an
alternative source for the open order behind the risk (the late one first),
a quote round when nothing is on order. Both go through the sourcing agent
and end in an approval.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.auth import Approver, Principal, Viewer
from director.api.sourcing import DispatchResponse
from director.desk.sourcing import SourcingDispatcher
from sc_core.schema.a2a import SourcingTask
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.idempotency import new_id

router = APIRouter(prefix="/risk", tags=["risk"])


@runtime_checkable
class RiskSource(Protocol):
    async def report(self, *, warehouse_code: str | None = None) -> dict[str, Any]: ...


class HttpRiskSource:
    """The planner's ``GET /planning/risk`` behind its bearer token."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 120.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_seconds,
        )

    async def report(self, *, warehouse_code: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if warehouse_code:
            params["warehouse_code"] = warehouse_code
        try:
            response = await self._http.get("/planning/risk", params=params)
        except httpx.HTTPError as exc:
            raise ScError("the planner did not answer", details={"error": str(exc)}) from exc
        if response.status_code != 200:
            raise ScError(
                f"the planner answered {response.status_code} for the risk radar",
                details={"status": response.status_code},
            )
        data: dict[str, Any] = response.json()
        return data

    async def aclose(self) -> None:
        await self._http.aclose()


class ActRequest(StrictModel):
    qty: float | None = Field(default=None, gt=0, description="override the suggested quantity")
    po_name: str | None = Field(default=None, description="the late order to source elsewhere")


@router.get("", response_model=dict[str, Any])
async def risk_radar(
    warehouse_code: str | None = Query(default=None),
    _: Principal = Viewer,
    source: RiskSource = Injected(RiskSource),  # type: ignore[type-abstract]
) -> dict[str, Any]:
    try:
        return await source.report(warehouse_code=warehouse_code)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc


@router.post("/{product_id}/act", response_model=DispatchResponse, status_code=202)
async def act_on_risk(
    product_id: int,
    body: ActRequest,
    principal: Principal = Approver,
    source: RiskSource = Injected(RiskSource),  # type: ignore[type-abstract]
    dispatcher: SourcingDispatcher = Injected(SourcingDispatcher),
) -> DispatchResponse:
    """One click: an alternative source for the order behind the risk, or a quote round."""
    try:
        report = await source.report()
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    product = next(
        (p for p in report.get("products", []) if p.get("product_id") == product_id), None
    )
    if product is None:
        raise HTTPException(status_code=404, detail=f"product {product_id} is not on the radar")
    # The order behind the risk: the late one when there is one, else the open one that
    # will not cover the demand in time. Without an order there is nothing to re-source.
    behind = body.po_name or next(
        iter((product.get("late_po_names") or []) + (product.get("open_po_names") or [])), None
    )
    reason = (
        f"stockout risk {round(100 * float(product.get('p_stockout_30') or 0))}% at 30 days, "
        f"from the risk radar by {principal.email}"
    )
    if behind:
        task = SourcingTask(
            kind="alternate_source",
            case_id=new_id("risk"),
            po_name=behind,
            product_id=product_id,
            reason=reason,
        )
    else:
        qty = body.qty or float(product.get("suggested_qty") or 0) or 1.0
        task = SourcingTask(
            kind="quote_round",
            case_id=new_id("risk"),
            product_id=product_id,
            qty=qty,
            reason=reason,
        )
    logger.bind(product_id=product_id, kind=task.kind, by=principal.email).info(
        "risk acted on from the Control Tower"
    )
    return DispatchResponse(**await dispatcher.run(task, requested_by=principal.email))
