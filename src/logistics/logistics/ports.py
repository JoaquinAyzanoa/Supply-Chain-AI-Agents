"""What the graph needs from Odoo, Graph and the app database.

The supplier agent's ``LivePorts`` already knows how to load an order, read
an email and send a draft; the logistics ports delegate those calls to it
and add the receipt reads and the date writes with source ``tracking``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol, cast

from logistics import AGENT_NAME
from logistics.models import ReceiptLineView, ReceiptView
from sc_core.mail.models import MessageIds, OutboundMessage
from sc_core.odoo.models import RunStatus
from sc_core.odoo.repositories import (
    AgentRunRepo,
    PickingRepo,
    PurchaseOrderRepo,
    StockMoveLineRepo,
)
from sc_core.odoo.repositories.picking import OPEN_PICKING_STATES
from supplier_comms.models import InboundMeta, PoContext
from supplier_comms.ports import LivePorts


class LogisticsPorts(Protocol):
    # --- reads -------------------------------------------------------------------
    async def load_po(self, po_name: str) -> PoContext | None: ...

    async def load_receipt(self, picking_id: int) -> ReceiptView | None: ...

    async def open_pickings(self, po_id: int) -> list[int]: ...

    async def inbound_text(self, message_id: str) -> str: ...

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]: ...

    async def inbound_meta(self, message_id: str) -> InboundMeta: ...

    # --- mail ----------------------------------------------------------------------
    async def create_draft(self, message: OutboundMessage) -> MessageIds: ...

    async def send_draft(self, draft_id: str) -> None: ...

    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, html_body: str | None = None
    ) -> None: ...

    async def find_sent(self, internet_message_id: str) -> MessageIds | None: ...

    async def record_outbound(self, *, ids: MessageIds, po_name: str, case_id: str) -> None: ...

    async def link_outbound(self, po_id: int, ids: MessageIds, *, case_id: str) -> None: ...

    # --- writes to Odoo (after approval only) -------------------------------------
    async def post_note(self, po_id: int, html: str) -> None: ...

    async def set_line_date(self, line_id: int, new_date: date, *, run_id: str) -> None: ...

    async def set_picking_date(self, picking_id: int, new_date: date, *, run_id: str) -> None: ...

    async def set_eta_meta(self, po_id: int, *, confidence: float) -> None: ...

    # --- run log --------------------------------------------------------------------
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


class LiveLogisticsPorts:
    def __init__(
        self,
        base: LivePorts,
        *,
        pickings: PickingRepo,
        move_lines: StockMoveLineRepo,
        purchase_orders: PurchaseOrderRepo,
        agent_runs: AgentRunRepo,
    ) -> None:
        self._base = base
        self._pickings = pickings
        self._move_lines = move_lines
        self._pos = purchase_orders
        self._runs = agent_runs

    # --- reads -------------------------------------------------------------------

    async def load_po(self, po_name: str) -> PoContext | None:
        return await self._base.load_po(po_name)

    async def load_receipt(self, picking_id: int) -> ReceiptView | None:
        picking = await self._pickings.get(picking_id)
        moves = await self._pickings.moves(picking_id)
        counted = await self._move_lines.for_picking(picking_id)
        po = await self._pos.get(picking.purchase_id.id) if picking.purchase_id else None
        po_lines = {line.id: line for line in await self._pos.lines(po.id)} if po else {}
        lines: list[ReceiptLineView] = []
        for move in moves:
            received = sum(
                ml.quantity for ml in counted if ml.move_id is not None and ml.move_id.id == move.id
            )
            po_line = (
                po_lines.get(move.purchase_line_id.id)
                if move.purchase_line_id is not None
                else None
            )
            lines.append(
                ReceiptLineView(
                    move_id=move.id,
                    po_line_id=po_line.id if po_line else None,
                    product=move.product_id.name,
                    product_id=move.product_id.id,
                    expected=move.product_uom_qty,
                    received=received,
                    uom=po_line.product_uom.name if po_line and po_line.product_uom else None,
                    ordered=po_line.product_qty if po_line else None,
                    received_total=po_line.qty_received if po_line else None,
                )
            )
        return ReceiptView(
            picking_id=picking.id,
            name=picking.name,
            state=picking.state,
            po_id=po.id if po else None,
            po_name=po.name if po else None,
            partner_id=picking.partner_id.id if picking.partner_id else None,
            partner_name=picking.partner_id.name if picking.partner_id else None,
            scheduled_date=picking.scheduled_date.date() if picking.scheduled_date else None,
            date_done=picking.date_done,
            lines=lines,
        )

    async def open_pickings(self, po_id: int) -> list[int]:
        return [
            p.id
            for p in await self._pickings.incoming_for_po(po_id)
            if p.state in OPEN_PICKING_STATES
        ]

    async def inbound_text(self, message_id: str) -> str:
        return await self._base.inbound_text(message_id)

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]:
        return await self._base.attachments_text(message_id, max_chars=max_chars)

    async def inbound_meta(self, message_id: str) -> InboundMeta:
        return await self._base.inbound_meta(message_id)

    # --- mail ----------------------------------------------------------------------

    async def create_draft(self, message: OutboundMessage) -> MessageIds:
        return await self._base.create_draft(message)

    async def send_draft(self, draft_id: str) -> None:
        await self._base.send_draft(draft_id)

    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, html_body: str | None = None
    ) -> None:
        await self._base.update_draft(draft_id, subject=subject, html_body=html_body)

    async def find_sent(self, internet_message_id: str) -> MessageIds | None:
        return await self._base.find_sent(internet_message_id)

    async def record_outbound(self, *, ids: MessageIds, po_name: str, case_id: str) -> None:
        await self._base.record_outbound(ids=ids, po_name=po_name, case_id=case_id)

    async def link_outbound(self, po_id: int, ids: MessageIds, *, case_id: str) -> None:
        await self._base.link_outbound(po_id, ids, case_id=case_id)

    # --- writes ----------------------------------------------------------------------

    async def post_note(self, po_id: int, html: str) -> None:
        await self._base.post_note(po_id, html)

    async def set_line_date(self, line_id: int, new_date: date, *, run_id: str) -> None:
        await self._pos.set_line_date_planned(
            line_id, _noon(new_date), source="tracking", run_id=run_id
        )

    async def set_picking_date(self, picking_id: int, new_date: date, *, run_id: str) -> None:
        await self._pickings.set_scheduled_date(
            picking_id, _noon(new_date), source="tracking", run_id=run_id
        )

    async def set_eta_meta(self, po_id: int, *, confidence: float) -> None:
        await self._pos.set_eta_meta(po_id, source="tracking", confidence=confidence)

    # --- run log --------------------------------------------------------------------

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


def _noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
