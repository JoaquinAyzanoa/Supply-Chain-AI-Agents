"""``GET /sourcing/rounds*`` and ``GET /sourcing/negotiations`` behind the bearer token.

The director reads these for the Control Tower (Supplier 360, the award
card) and for its hourly job: which rounds are due for a comparison.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi_injector import Injected

from sc_core.infra.settings import Settings
from sourcing.domain.models import Negotiation, Round
from sourcing.infra.store import RoundStore

router = APIRouter(prefix="/sourcing", tags=["sourcing"])


def _check(settings: Settings, authorization: str) -> None:
    if not settings.a2a_token or authorization != f"Bearer {settings.a2a_token}":
        raise HTTPException(status_code=401, detail="bearer token required")


@router.get("/rounds", response_model=list[Round])
async def rounds(
    status: str | None = Query(default=None, description="active | open | awarded | ..."),
    partner_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    store: RoundStore = Injected(RoundStore),  # type: ignore[type-abstract]
) -> list[Round]:
    _check(settings, authorization)
    return await store.rounds(status=status, partner_id=partner_id, limit=limit)


@router.get("/rounds/due", response_model=list[Round])
async def due_rounds(
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    store: RoundStore = Injected(RoundStore),  # type: ignore[type-abstract]
) -> list[Round]:
    """Open rounds past their deadline, or whose every invitation was answered."""
    _check(settings, authorization)
    return await store.due_rounds(datetime.now(UTC))


@router.get("/rounds/{round_id}", response_model=Round)
async def get_round(
    round_id: int,
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    store: RoundStore = Injected(RoundStore),  # type: ignore[type-abstract]
) -> Round:
    _check(settings, authorization)
    found = await store.get_round(round_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"round {round_id} not found")
    return found


@router.get("/negotiations", response_model=list[Negotiation])
async def negotiations(
    po_name: str = Query(...),
    product_id: int | None = Query(default=None),
    authorization: str = Header(default=""),
    settings: Settings = Injected(Settings),
    store: RoundStore = Injected(RoundStore),  # type: ignore[type-abstract]
) -> list[Negotiation]:
    _check(settings, authorization)
    return await store.negotiations_for(po_name, product_id)
