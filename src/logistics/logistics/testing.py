"""In-memory ports for graph tests: the supplier agent's fake plus receipts and date writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from logistics.models import ReceiptLineView, ReceiptView
from sc_core.mail.models import MessageIds, OutboundMessage
from supplier_comms.models import InboundMeta, PoContext
from supplier_comms.testing import FakePorts, demo_context

__all__ = ["FakeLogisticsPorts", "demo_context", "demo_receipt"]


def demo_receipt(*, short_by: float = 0.0, extra_line: bool = False) -> ReceiptView:
    """WH/IN/00042 for the demo order: two lines, optionally short on the pump."""
    lines = [
        ReceiptLineView(
            move_id=501,
            po_line_id=31,
            product="Bomba hidráulica 2HP",
            product_id=101,
            expected=2.0,
            received=2.0 - short_by,
            uom="Unidades",
            ordered=2.0,
            received_total=2.0 - short_by,
        ),
        ReceiptLineView(
            move_id=502,
            po_line_id=32,
            product='Manguera 1/2"',
            product_id=102,
            expected=20.0,
            received=20.0,
            uom="m",
            ordered=20.0,
            received_total=20.0,
        ),
    ]
    if extra_line:
        lines.append(
            ReceiptLineView(
                move_id=503,
                po_line_id=None,
                product="Filtro de retorno",
                product_id=103,
                expected=0.0,
                received=1.0,
                uom="Unidades",
            )
        )
    return ReceiptView(
        picking_id=42,
        name="WH/IN/00042",
        state="done",
        po_id=7,
        po_name="P00015",
        partner_id=42,
        partner_name="Proveedor Hidraulica",
        scheduled_date=date(2026, 10, 1),
        date_done=datetime(2026, 10, 2, 15, 0, tzinfo=UTC),
        lines=lines,
    )


@dataclass
class FakeLogisticsPorts:
    base: FakePorts = field(default_factory=FakePorts)
    receipts: dict[int, ReceiptView] = field(default_factory=dict)
    open_picking_ids: dict[int, list[int]] = field(default_factory=dict)
    picking_dates: list[dict[str, Any]] = field(default_factory=list)
    line_dates: list[dict[str, Any]] = field(default_factory=list)
    eta_meta: list[dict[str, Any]] = field(default_factory=list)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # --- reads -------------------------------------------------------------------
    async def load_po(self, po_name: str) -> PoContext | None:
        return await self.base.load_po(po_name)

    async def load_receipt(self, picking_id: int) -> ReceiptView | None:
        return self.receipts.get(picking_id)

    async def open_pickings(self, po_id: int) -> list[int]:
        return list(self.open_picking_ids.get(po_id, []))

    async def inbound_text(self, message_id: str) -> str:
        return await self.base.inbound_text(message_id)

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]:
        return await self.base.attachments_text(message_id, max_chars=max_chars)

    async def inbound_meta(self, message_id: str) -> InboundMeta:
        return await self.base.inbound_meta(message_id)

    # --- mail ----------------------------------------------------------------------
    async def create_draft(self, message: OutboundMessage) -> MessageIds:
        return await self.base.create_draft(message)

    async def send_draft(self, draft_id: str) -> None:
        await self.base.send_draft(draft_id)

    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, html_body: str | None = None
    ) -> None:
        await self.base.update_draft(draft_id, subject=subject, html_body=html_body)

    async def find_sent(self, internet_message_id: str) -> MessageIds | None:
        return await self.base.find_sent(internet_message_id)

    async def record_outbound(self, *, ids: MessageIds, po_name: str, case_id: str) -> None:
        await self.base.record_outbound(ids=ids, po_name=po_name, case_id=case_id)

    async def link_outbound(self, po_id: int, ids: MessageIds, *, case_id: str) -> None:
        await self.base.link_outbound(po_id, ids, case_id=case_id)

    # --- writes ----------------------------------------------------------------------
    async def post_note(self, po_id: int, html: str) -> None:
        await self.base.post_note(po_id, html)

    async def set_line_date(self, line_id: int, new_date: date, *, run_id: str) -> None:
        self.line_dates.append(
            {"line_id": line_id, "date": new_date, "run_id": run_id, "source": "tracking"}
        )

    async def set_picking_date(self, picking_id: int, new_date: date, *, run_id: str) -> None:
        self.picking_dates.append({"picking_id": picking_id, "date": new_date, "run_id": run_id})

    async def set_eta_meta(self, po_id: int, *, confidence: float) -> None:
        self.eta_meta.append({"po_id": po_id, "confidence": confidence, "source": "tracking"})

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
        self.runs[run_id] = {"case_id": case_id, "po_id": po_id, "status": "running"}

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None:
        self.runs.setdefault(run_id, {})
        self.runs[run_id].update(status=status, summary=summary, usage=usage or {})
