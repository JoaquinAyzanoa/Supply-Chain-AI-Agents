"""What was actually received, line by line (``stock.move.line``).

A picking's moves say what was expected; its move lines say what the clerk
counted (``quantity``, ``picked``). Reconciliation and supplier performance
read these, never the demand.
"""

from __future__ import annotations

from datetime import datetime

from sc_core.odoo.models import StockMoveLine, to_odoo_datetime
from sc_core.odoo.repositories.base import Repo


class StockMoveLineRepo(Repo[StockMoveLine]):
    model = StockMoveLine

    async def for_picking(self, picking_id: int) -> list[StockMoveLine]:
        return await self.find([["picking_id", "=", picking_id]], order="id asc")

    async def received_for_po(self, po_id: int) -> list[StockMoveLine]:
        """Done lines of every receipt on the order (returns excluded)."""
        return await self.find(
            [
                ["move_id.purchase_line_id.order_id", "=", po_id],
                ["picking_id.picking_type_code", "=", "incoming"],
                ["state", "=", "done"],
            ],
            order="date asc, id asc",
        )

    async def received_between(
        self, since: datetime, until: datetime, *, partner_id: int | None = None
    ) -> list[StockMoveLine]:
        """Done receipt lines in a period, for the weekly performance run."""
        domain: list[object] = [
            ["picking_id.picking_type_code", "=", "incoming"],
            ["state", "=", "done"],
            ["date", ">=", to_odoo_datetime(since)],
            ["date", "<", to_odoo_datetime(until)],
        ]
        if partner_id is not None:
            domain.append(["picking_id.partner_id", "=", partner_id])
        return await self.find(domain, order="date asc, id asc", limit=5000)
