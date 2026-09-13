"""In-memory ports for graph tests: the supplier agent's fake plus bills and candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from invoice_match.models import BillLineView, BillView, PoCandidate
from supplier_comms.models import InboundMeta, PoContext
from supplier_comms.testing import FakePorts, demo_context

__all__ = ["FakeInvoicePorts", "demo_bill", "demo_context"]


def demo_bill(*, price_unit: float = 500.0, po_name: str | None = "P00015") -> BillView:
    """A bill someone typed in Odoo for the demo order's pump line."""
    return BillView(
        move_id=90,
        name="BILL/2026/10/0003",
        ref="F001-000777",
        invoice_date=date(2026, 10, 3),
        partner_id=42,
        partner_name="Proveedor Hidraulica",
        amount_untaxed=price_unit * 2,
        amount_total=price_unit * 2 * 1.18,
        currency="PEN",
        state="draft",
        po_name=po_name,
        lines=[
            BillLineView(
                description="Bomba hidráulica 2HP",
                product_id=101,
                qty=2.0,
                price_unit=price_unit,
                total=price_unit * 2,
                purchase_line_id=31,
            )
        ],
    )


@dataclass
class FakeInvoicePorts:
    base: FakePorts = field(default_factory=FakePorts)
    bills: dict[int, BillView] = field(default_factory=dict)
    candidate_orders: dict[int, list[PoCandidate]] = field(default_factory=dict)
    existing: dict[tuple[int, str], tuple[int, str | None]] = field(default_factory=dict)
    created: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)  # what the invoice form shows
    bill_notes: list[tuple[int, str]] = field(default_factory=list)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def load_po(self, po_name: str) -> PoContext | None:
        return await self.base.load_po(po_name)

    async def load_bill(self, move_id: int) -> BillView | None:
        return self.bills.get(move_id)

    async def candidates(self, partner_id: int) -> list[PoCandidate]:
        return list(self.candidate_orders.get(partner_id, []))

    async def existing_bill(self, partner_id: int, ref: str) -> tuple[int, str | None] | None:
        return self.existing.get((partner_id, ref))

    async def inbound_text(self, message_id: str) -> str:
        return await self.base.inbound_text(message_id)

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]:
        return await self.base.attachments_text(message_id, max_chars=max_chars)

    async def inbound_meta(self, message_id: str) -> InboundMeta:
        return await self.base.inbound_meta(message_id)

    async def partner_by_email(self, address: str) -> int | None:
        return await self.base.partner_by_email(address)

    async def create_draft_bill(
        self, po_id: int, *, ref: str | None, invoice_date: date | None
    ) -> tuple[int, str | None]:
        self.created.append({"po_id": po_id, "ref": ref, "invoice_date": invoice_date})
        return 500 + len(self.created), f"BILL/2026/10/{len(self.created):04d}"

    async def post_note(self, po_id: int, html: str) -> None:
        await self.base.post_note(po_id, html)

    async def record_check(
        self, move_id: int, *, verdict: str, po_id: int | None, summary: str
    ) -> None:
        self.checks.append(
            {"move_id": move_id, "verdict": verdict, "po_id": po_id, "summary": summary}
        )

    async def post_bill_note(self, move_id: int, html: str) -> None:
        self.bill_notes.append((move_id, html))

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
