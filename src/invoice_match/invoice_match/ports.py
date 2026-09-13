"""What the graph needs: the supplier agent's ports for the order and the email, plus bills.

Bills are read and created in draft through ``AccountMoveRepo``; nothing
here can post one.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol, cast

from invoice_match import AGENT_NAME
from invoice_match.models import BillLineView, BillView, PoCandidate
from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import RunStatus
from sc_core.odoo.repositories import AccountMoveRepo, AgentRunRepo
from supplier_comms.models import InboundMeta, PoContext
from supplier_comms.ports import LivePorts


class InvoicePorts(Protocol):
    async def load_po(self, po_name: str) -> PoContext | None: ...

    async def load_bill(self, move_id: int) -> BillView | None: ...

    async def candidates(self, partner_id: int) -> list[PoCandidate]: ...

    async def existing_bill(self, partner_id: int, ref: str) -> tuple[int, str | None] | None: ...

    async def inbound_text(self, message_id: str) -> str: ...

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]: ...

    async def inbound_meta(self, message_id: str) -> InboundMeta: ...

    async def partner_by_email(self, address: str) -> int | None: ...

    async def create_draft_bill(
        self, po_id: int, *, ref: str | None, invoice_date: date | None
    ) -> tuple[int, str | None]: ...

    async def post_note(self, po_id: int, html: str) -> None: ...

    async def start_run(
        self,
        *,
        run_id: str,
        case_id: str,
        po_id: int | None,
        model: str | None,
        trace_url: str | None,
    ) -> None: ...

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None: ...


class LiveInvoicePorts:
    def __init__(
        self,
        base: LivePorts,
        *,
        odoo: OdooClient,
        bills: AccountMoveRepo,
        agent_runs: AgentRunRepo,
    ) -> None:
        self._base = base
        self._odoo = odoo
        self._bills = bills
        self._runs = agent_runs

    async def load_po(self, po_name: str) -> PoContext | None:
        return await self._base.load_po(po_name)

    async def load_bill(self, move_id: int) -> BillView | None:
        bill = await self._bills.get(move_id)
        lines = await self._bills.lines(move_id)
        po_name = None
        if bill.purchase_id is not None:
            po_name = bill.purchase_id.name
        elif lines and lines[0].purchase_line_id is not None:
            rows = await self._odoo.read(
                "purchase.order.line", [lines[0].purchase_line_id.id], ["order_id"]
            )
            order = rows[0].get("order_id") if rows else None
            if isinstance(order, list | tuple) and len(order) == 2:
                po_name = str(order[1])
        return BillView(
            move_id=bill.id,
            name=bill.name,
            ref=bill.ref,
            invoice_date=bill.invoice_date,
            partner_id=bill.partner_id.id if bill.partner_id else None,
            partner_name=bill.partner_id.name if bill.partner_id else None,
            amount_untaxed=bill.amount_untaxed,
            amount_total=bill.amount_total,
            currency=bill.currency_id.name if bill.currency_id else None,
            state=bill.state,
            po_name=po_name,
            lines=[
                BillLineView(
                    description=line.name or (line.product_id.name if line.product_id else ""),
                    product_id=line.product_id.id if line.product_id else None,
                    qty=line.quantity,
                    price_unit=line.price_unit,
                    total=line.price_subtotal,
                    purchase_line_id=line.purchase_line_id.id if line.purchase_line_id else None,
                )
                for line in lines
            ],
        )

    async def candidates(self, partner_id: int) -> list[PoCandidate]:
        rows = await self._odoo.search_read(
            "purchase.order",
            [
                ["partner_id", "=", partner_id],
                ["state", "in", ["purchase", "done"]],
                ["invoice_status", "in", ["to invoice", "no"]],
            ],
            ["name", "state", "amount_total", "amount_untaxed", "currency_id"],
            order="date_approve desc, id desc",
            limit=50,
        )
        out = []
        for row in rows:
            currency = row.get("currency_id")
            out.append(
                PoCandidate(
                    id=int(row["id"]),
                    name=str(row["name"]),
                    state=str(row["state"]),
                    amount_total=float(row.get("amount_total") or 0.0),
                    amount_untaxed=float(row.get("amount_untaxed") or 0.0),
                    currency=str(currency[1]) if isinstance(currency, list | tuple) else None,
                )
            )
        return out

    async def existing_bill(self, partner_id: int, ref: str) -> tuple[int, str | None] | None:
        bill = await self._bills.find_by_ref(partner_id, ref)
        return (bill.id, bill.name) if bill else None

    async def inbound_text(self, message_id: str) -> str:
        return await self._base.inbound_text(message_id)

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]:
        return await self._base.attachments_text(message_id, max_chars=max_chars)

    async def inbound_meta(self, message_id: str) -> InboundMeta:
        return await self._base.inbound_meta(message_id)

    async def partner_by_email(self, address: str) -> int | None:
        return await self._base.partner_by_email(address)

    async def create_draft_bill(
        self, po_id: int, *, ref: str | None, invoice_date: date | None
    ) -> tuple[int, str | None]:
        bill = await self._bills.create_draft_bill(po_id, ref=ref, invoice_date=invoice_date)
        return bill.id, bill.name

    async def post_note(self, po_id: int, html: str) -> None:
        await self._base.post_note(po_id, html)

    async def start_run(
        self,
        *,
        run_id: str,
        case_id: str,
        po_id: int | None,
        model: str | None,
        trace_url: str | None,
    ) -> None:
        await self._runs.start(
            run_id=run_id,
            agent=AGENT_NAME,
            case_id=case_id,
            po_id=po_id,
            model=model,
            trace_url=trace_url,
        )

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None:
        await self._runs.finish(run_id, cast(RunStatus, status), summary[:500], usage)
