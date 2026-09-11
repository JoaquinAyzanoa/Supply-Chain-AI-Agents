"""Receipts and their moves (``stock.picking``, ``stock.move``)."""

from __future__ import annotations

from datetime import date, datetime

from sc_core.odoo.models import EtaSource, Picking, StockMove, to_odoo_date, to_odoo_datetime
from sc_core.odoo.repositories.base import Repo

OPEN_PICKING_STATES = ["draft", "waiting", "confirmed", "assigned"]


class PickingRepo(Repo[Picking]):
    model = Picking

    async def incoming_for_po(self, po_id: int) -> list[Picking]:
        return await self.find(
            [["purchase_id", "=", po_id], ["picking_type_code", "=", "incoming"]],
            order="scheduled_date asc",
        )

    async def late_incoming(self, as_of: date) -> list[Picking]:
        return await self.find(
            [
                ["picking_type_code", "=", "incoming"],
                ["state", "in", OPEN_PICKING_STATES],
                ["scheduled_date", "<", to_odoo_date(as_of)],
            ],
            order="scheduled_date asc",
        )

    async def moves(self, picking_id: int) -> list[StockMove]:
        rows = await self._c.search_read(
            StockMove.ODOO_MODEL, [["picking_id", "=", picking_id]], StockMove.odoo_fields()
        )
        return [StockMove.from_odoo(r) for r in rows]

    async def set_scheduled_date(
        self, picking_id: int, new_date: datetime, *, source: EtaSource, run_id: str
    ) -> Picking:
        await self._write(
            [picking_id],
            {
                "scheduled_date": to_odoo_datetime(new_date),
                "sc_eta_source": source,
                "sc_last_run_id": run_id,
            },
        )
        return await self.get(picking_id)

    async def set_needs_human(self, picking_id: int, value: bool) -> None:
        await self._write([picking_id], {"sc_needs_human": value})
