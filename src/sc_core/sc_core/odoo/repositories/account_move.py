"""Vendor bills (``account.move`` with ``move_type = in_invoice``).

The agents read bills and create them in draft from a purchase order. They
never post one: ``action_post`` is not exposed here on purpose, so accounting
stays with people (see ``tests/unit/odoo/test_repositories.py``).
"""

from __future__ import annotations

from datetime import date

from sc_core.odoo.models import AccountMove, AccountMoveLine, to_odoo_date
from sc_core.odoo.repositories.base import Repo
from sc_core.shared.errors import ScError

BILL_TYPES = ["in_invoice", "in_refund"]


class AccountMoveRepo(Repo[AccountMove]):
    model = AccountMove

    async def bills_for_partner(self, partner_id: int, *, limit: int = 50) -> list[AccountMove]:
        """Newest first; every state, so a held or posted bill is found too."""
        return await self.find(
            [["move_type", "in", BILL_TYPES], ["partner_id", "=", partner_id]],
            order="invoice_date desc, id desc",
            limit=limit,
        )

    async def bills_for_po(
        self, po_id: int, *, states: list[str] | None = None
    ) -> list[AccountMove]:
        """Bills whose lines come from this order (Odoo links them through the order lines)."""
        domain: list[object] = [
            ["move_type", "in", BILL_TYPES],
            ["invoice_line_ids.purchase_line_id.order_id", "=", po_id],
        ]
        if states:
            domain.append(["state", "in", states])
        return await self.find(domain, order="id desc")

    async def find_by_ref(self, partner_id: int, ref: str) -> AccountMove | None:
        """The supplier's invoice number, as typed on the bill; ``None`` when unknown."""
        return await self.find_one(
            [["move_type", "in", BILL_TYPES], ["partner_id", "=", partner_id], ["ref", "=", ref]]
        )

    async def lines(self, move_id: int) -> list[AccountMoveLine]:
        rows = await self._c.search_read(
            AccountMoveLine.ODOO_MODEL,
            [["move_id", "=", move_id], ["display_type", "=", "product"]],
            AccountMoveLine.odoo_fields(),
            order="id asc",
        )
        return [AccountMoveLine.from_odoo(r) for r in rows]

    async def create_draft_bill(
        self, po_id: int, *, ref: str | None = None, invoice_date: date | None = None
    ) -> AccountMove:
        """A draft bill for what the order has to invoice, through Odoo's own action.

        Idempotent: an existing draft bill on the order is returned instead of a
        second one. The bill is left in draft; nobody here posts it.
        """
        existing = await self.bills_for_po(po_id, states=["draft"])
        if existing:
            bill = existing[0]
        else:
            await self._c.call("purchase.order", "action_create_invoice", [po_id])
            created = await self.bills_for_po(po_id, states=["draft"])
            if not created:
                raise ScError(
                    "Odoo created no draft bill for the order",
                    details={"po_id": po_id, "hint": "nothing left to invoice?"},
                )
            bill = created[0]
        values: dict[str, object] = {}
        if ref and bill.ref != ref:
            values["ref"] = ref
        if invoice_date and bill.invoice_date != invoice_date:
            values["invoice_date"] = to_odoo_date(invoice_date)
        if values:
            await self._write([bill.id], values)
            bill = await self.get(bill.id)
        return bill
