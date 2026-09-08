"""Reorder rules (``stock.warehouse.orderpoint``)."""

from __future__ import annotations

from typing import Any

from sc_core.odoo.models import Orderpoint
from sc_core.odoo.repositories.base import Repo
from sc_core.shared.errors import ValidationFailed


class OrderpointRepo(Repo[Orderpoint]):
    model = Orderpoint

    async def list(self, *, warehouse_id: int | None = None) -> list[Orderpoint]:
        domain: list[Any] = []
        if warehouse_id is not None:
            domain.append(["warehouse_id", "=", warehouse_id])
        return await self.find(domain, order="product_id asc")

    async def for_product(
        self, product_id: int, *, warehouse_id: int | None = None
    ) -> Orderpoint | None:
        domain: list[Any] = [["product_id", "=", product_id]]
        if warehouse_id is not None:
            domain.append(["warehouse_id", "=", warehouse_id])
        return await self.find_one(domain)

    async def set_min_max(
        self, orderpoint_id: int, *, minimum: float, maximum: float
    ) -> Orderpoint:
        _validate(minimum, maximum)
        await self._write([orderpoint_id], {"product_min_qty": minimum, "product_max_qty": maximum})
        return await self.get(orderpoint_id)

    async def create(
        self,
        *,
        product_id: int,
        warehouse_id: int,
        location_id: int,
        minimum: float,
        maximum: float,
        trigger: str = "auto",
    ) -> Orderpoint:
        _validate(minimum, maximum)
        new_id = await self._c.create(
            self._name,
            {
                "product_id": product_id,
                "warehouse_id": warehouse_id,
                "location_id": location_id,
                "product_min_qty": minimum,
                "product_max_qty": maximum,
                "trigger": trigger,
            },
        )
        return await self.get(new_id)

    async def triggered(self) -> list[Orderpoint]:
        """Rules whose forecast is below the minimum (something to order)."""
        return await self.find([["qty_to_order", ">", 0]], order="qty_to_order desc")


def _validate(minimum: float, maximum: float) -> None:
    if minimum < 0 or maximum < 0:
        raise ValidationFailed("reorder quantities cannot be negative")
    if maximum < minimum:
        raise ValidationFailed("maximum must be greater than or equal to minimum")
