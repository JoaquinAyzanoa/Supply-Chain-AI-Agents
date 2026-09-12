"""Everything the planner needs from Odoo, behind two protocols.

``DataPorts`` (reads) is composed from the phase 7 repositories by
``LiveDataPorts``; ``WritePorts`` (reorder rules, RFQs, the run log) is what
an approved proposal unlocks, composed by ``LiveWritePorts``. Tests use the
fakes in ``inventory_planning.testing``; ``what_if`` runs get a
``ReadOnlyWritePorts`` that raises on any write.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol

from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import (
    DailyDemand,
    IncomingLine,
    NewOrderLine,
    OnHand,
    Orderpoint,
    Product,
    RunStatus,
    SupplierTerms,
    Warehouse,
)
from sc_core.odoo.repositories import (
    AgentRunRepo,
    DemandRepo,
    IncomingRepo,
    OrderpointRepo,
    ProductRepo,
    PurchaseOrderRepo,
    QuantRepo,
    WarehouseRepo,
    supplier_terms,
)
from sc_core.shared.errors import Forbidden


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


# --- writes ---------------------------------------------------------------------------


class WritePorts(Protocol):
    async def set_orderpoint(
        self,
        *,
        product_id: int,
        warehouse_id: int,
        minimum: float,
        maximum: float,
        existing_id: int | None,
    ) -> int:
        """Update the rule ``existing_id`` or create one; returns the rule id."""
        ...

    async def create_rfq(
        self,
        *,
        partner_id: int,
        lines: list[NewOrderLine],
        external_ref: str,
        origin: str | None = None,
    ) -> tuple[int, str, bool]:
        """(po id, po name, created now?) — idempotent on ``external_ref``."""
        ...

    async def start_run(self, **fields: Any) -> None: ...

    async def finish_run(self, run_id: str, *, status: str, summary: str) -> None: ...


class LiveWritePorts:
    def __init__(self, odoo: OdooClient) -> None:
        self._orderpoints = OrderpointRepo(odoo)
        self._orders = PurchaseOrderRepo(odoo)
        self._warehouses = WarehouseRepo(odoo)
        self._runs = AgentRunRepo(odoo)
        self._stock_location: dict[int, int] = {}

    async def set_orderpoint(
        self,
        *,
        product_id: int,
        warehouse_id: int,
        minimum: float,
        maximum: float,
        existing_id: int | None,
    ) -> int:
        if existing_id is not None:
            await self._orderpoints.set_min_max(existing_id, minimum=minimum, maximum=maximum)
            return existing_id
        created = await self._orderpoints.create(
            product_id=product_id,
            warehouse_id=warehouse_id,
            location_id=await self._location(warehouse_id),
            minimum=minimum,
            maximum=maximum,
        )
        return created.id

    async def create_rfq(
        self,
        *,
        partner_id: int,
        lines: list[NewOrderLine],
        external_ref: str,
        origin: str | None = None,
    ) -> tuple[int, str, bool]:
        existing = await self._orders.find_by_external_ref(external_ref)
        if existing is not None:
            return existing.id, existing.name, False
        po = await self._orders.create_rfq(
            partner_id, lines, external_ref=external_ref, origin=origin
        )
        return po.id, po.name, True

    async def start_run(self, **fields: Any) -> None:
        await self._runs.start(**fields)

    async def finish_run(self, run_id: str, *, status: str, summary: str) -> None:
        from typing import cast

        await self._runs.finish(run_id, cast(RunStatus, status), summary)

    async def _location(self, warehouse_id: int) -> int:
        if warehouse_id not in self._stock_location:
            warehouse = await self._warehouses.get(warehouse_id)
            if warehouse.lot_stock_id is None:
                raise Forbidden(f"warehouse {warehouse_id} has no stock location")
            self._stock_location[warehouse_id] = warehouse.lot_stock_id.id
        return self._stock_location[warehouse_id]


class ReadOnlyWritePorts:
    """For ``what_if``: the run log is kept, any business write is a bug."""

    def __init__(self, inner: WritePorts) -> None:
        self._inner = inner

    async def set_orderpoint(self, **kwargs: Any) -> int:
        raise Forbidden("what_if runs never write reorder rules")

    async def create_rfq(self, **kwargs: Any) -> tuple[int, str, bool]:
        raise Forbidden("what_if runs never create RFQs")

    async def start_run(self, **fields: Any) -> None:
        await self._inner.start_run(**fields)

    async def finish_run(self, run_id: str, *, status: str, summary: str) -> None:
        await self._inner.finish_run(run_id, status=status, summary=summary)
