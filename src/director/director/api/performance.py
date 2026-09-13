"""Supplier scores and rankings for the Control Tower, read from the performance agent."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected

from director.api.auth import Principal, Viewer
from sc_core.schema.a2a import SupplierRanking, SupplierScore
from sc_core.shared.errors import ScError

router = APIRouter(prefix="/performance", tags=["performance"])


@runtime_checkable
class PerformanceSource(Protocol):
    async def scores(self) -> list[dict[str, Any]]: ...

    async def rank(self, product_id: int) -> dict[str, Any]: ...

    async def rank_many(self, product_ids: list[int]) -> list[dict[str, Any]]: ...


class HttpPerformanceSource:
    """The performance agent's ``GET /performance/*`` behind its bearer token."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 30.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_seconds,
        )

    async def scores(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = await self._get("/performance/scores")
        return data

    async def rank(self, product_id: int) -> dict[str, Any]:
        data: dict[str, Any] = await self._get(f"/performance/rank/{product_id}")
        return data

    async def rank_many(self, product_ids: list[int]) -> list[dict[str, Any]]:
        if not product_ids:
            return []
        ids = ",".join(str(pid) for pid in sorted(set(product_ids)))
        data: list[dict[str, Any]] = await self._get(
            "/performance/rank", params={"product_ids": ids}
        )
        return data

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        try:
            response = await self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ScError(
                "the performance agent did not answer", details={"error": str(exc)}
            ) from exc
        if response.status_code != 200:
            raise ScError(
                f"the performance agent answered {response.status_code}",
                details={"status": response.status_code, "path": path},
            )
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()


@router.get("/scores", response_model=list[SupplierScore])
async def supplier_scores(
    _: Principal = Viewer,
    source: PerformanceSource = Injected(PerformanceSource),  # type: ignore[type-abstract]
) -> list[SupplierScore]:
    try:
        rows = await source.scores()
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    return [SupplierScore.model_validate(row) for row in rows]


@router.get("/rank/{product_id}", response_model=SupplierRanking)
async def supplier_rank(
    product_id: int,
    _: Principal = Viewer,
    source: PerformanceSource = Injected(PerformanceSource),  # type: ignore[type-abstract]
) -> SupplierRanking:
    try:
        data = await source.rank(product_id)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    return SupplierRanking.model_validate(data)
