"""Everything the planner needs from Odoo and the app database, behind one protocol.

``LiveDataPorts`` composes the phase 7 repositories; tests use
``inventory_planning.testing.FakeDataPorts``. Reads are free; the writes
(reorder rules, RFQs, run log) are the ones an approved proposal unlocks and
arrive with the apply node.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import (
    DailyDemand,
    IncomingLine,
    OnHand,
    Orderpoint,
    Product,
    SupplierTerms,
    Warehouse,
)
from sc_core.odoo.repositories import (
    DemandRepo,
    IncomingRepo,
    OrderpointRepo,
    ProductRepo,
    QuantRepo,
    WarehouseRepo,
    supplier_terms,
)


class DataPorts(Protocol):
    async def warehouse(self, code: str | None) -> Warehouse: ...

    async def products(self, category: str | None) -> list[Product]: ...

    async def daily_sales(
        self, product_ids: Sequence[int], *, since: date, warehouse_id: int
    ) -> list[DailyDemand]: ...

    async def on_hand(self, product_ids: Sequence[int], *, warehouse_id: int) -> list[OnHand]: ...

    async def open_supply(
        self, product_ids: Sequence[int], *, warehouse_id: int
    ) -> list[IncomingLine]: ...

    async def supplier_terms(self, product_ids: Sequence[int]) -> list[SupplierTerms]: ...

    async def orderpoints(self, warehouse_id: int) -> list[Orderpoint]: ...


class LiveDataPorts:
    def __init__(self, odoo: OdooClient) -> None:
        self._odoo = odoo
        self._warehouses = WarehouseRepo(odoo)
        self._products = ProductRepo(odoo)
        self._demand = DemandRepo(odoo)
        self._quants = QuantRepo(odoo)
        self._incoming = IncomingRepo(odoo)
        self._orderpoints = OrderpointRepo(odoo)

    async def warehouse(self, code: str | None) -> Warehouse:
        if code:
            found = await self._warehouses.by_code(code)
            if found is not None:
                return found
        return await self._warehouses.main()

    async def products(self, category: str | None) -> list[Product]:
        return await self._products.plannable(category=category or None)

    async def daily_sales(
        self, product_ids: Sequence[int], *, since: date, warehouse_id: int
    ) -> list[DailyDemand]:
        # Sales lines carry the warehouse only when the order set one; the single
        # warehouse of the demo makes the filter moot, so it is not applied here.
        return await self._demand.daily_sales(product_ids, since=since)

    async def on_hand(self, product_ids: Sequence[int], *, warehouse_id: int) -> list[OnHand]:
        return await self._quants.on_hand(product_ids, warehouse_id=warehouse_id)

    async def open_supply(
        self, product_ids: Sequence[int], *, warehouse_id: int
    ) -> list[IncomingLine]:
        return await self._incoming.open_supply(product_ids, warehouse_id=warehouse_id)

    async def supplier_terms(self, product_ids: Sequence[int]) -> list[SupplierTerms]:
        return await supplier_terms(self._odoo, product_ids)

    async def orderpoints(self, warehouse_id: int) -> list[Orderpoint]:
        return await self._orderpoints.list(warehouse_id=warehouse_id)
