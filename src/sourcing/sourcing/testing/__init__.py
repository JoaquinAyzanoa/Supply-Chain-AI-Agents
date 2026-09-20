"""In-memory ports for the tests: an order, its suppliers, what Odoo and the supplier
agent would answer, and every write recorded."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sc_core.a2a import AgentReply
from sc_core.schema.a2a import Outcome, QuoteLine, SupplierCommsResult
from sc_core.shared.errors import ScError
from sourcing.domain.models import BasketLine, OrderRef, PriceEntry, RfqSnapshot, SupplierOption
from sourcing.infra.store import MemoryRoundStore

HIDRAULICA = 8
ALTERNA = 9
IMPORTADORA = 10
VALVE = 1  # [CBEA-LHN] Válvula de contrabalance


def demo_options() -> list[SupplierOption]:
    """The three suppliers of the seeded dataset as the ranking returns them."""
    return [
        SupplierOption(
            partner_id=HIDRAULICA,
            partner_name="Proveedor Hidraulica",
            has_email=True,
            score=82.0,
            rank=1,
            price=104.16,
            currency="USD",
            min_qty=1.0,
            lead_days=30,
        ),
        SupplierOption(
            partner_id=ALTERNA,
            partner_name="Hidráulica Alterna SAC",
            has_email=True,
            score=70.0,
            rank=2,
            price=114.24,
            currency="USD",
            min_qty=1.0,
            lead_days=18,
        ),
        SupplierOption(
            partner_id=IMPORTADORA,
            partner_name="Importadora del Sur SAC",
            has_email=False,
            score=None,
            rank=3,
            price=90.72,
            currency="USD",
            min_qty=20.0,
            lead_days=60,
            first_time=True,
        ),
    ]


def demo_entries() -> list[PriceEntry]:
    return [
        PriceEntry(
            partner_id=o.partner_id,
            product_id=VALVE,
            price=o.price or 0.0,
            currency="USD",
            min_qty=o.min_qty,
            lead_days=o.lead_days,
        )
        for o in demo_options()
    ]


def supplier_reply(kind: str, case_id: str, status: str, summary: str = "ok") -> AgentReply:
    result = SupplierCommsResult(
        kind=kind,  # type: ignore[arg-type]
        case_id=case_id,
        run_id="run_sc",
        outcome=Outcome(status=status, summary=summary),  # type: ignore[arg-type]
    )
    return AgentReply(
        status="input_required" if status == "awaiting_approval" else "completed",
        text=result.model_dump_json(),
    )


@dataclass
class FakeSourcingPorts(MemoryRoundStore):
    """The store plus everything the graph reads and writes, in memory."""

    orders: dict[str, OrderRef] = field(default_factory=dict)
    baskets: dict[str, list[BasketLine]] = field(default_factory=dict)
    products: dict[int, str] = field(default_factory=dict)
    options: list[SupplierOption] = field(default_factory=list)
    entries: list[PriceEntry] = field(default_factory=list)
    last_paid_by_product: dict[int, float] = field(default_factory=dict)
    rfqs: dict[int, RfqSnapshot] = field(default_factory=dict)
    supplier_replies: list[AgentReply | Exception] = field(default_factory=list)
    sent_tasks: list[dict[str, Any]] = field(default_factory=list)
    created_rfqs: list[dict[str, Any]] = field(default_factory=list)
    confirmed: list[int] = field(default_factory=list)
    cancelled: list[int] = field(default_factory=list)
    groups: list[list[int]] = field(default_factory=list)
    notes: list[tuple[int, str]] = field(default_factory=list)
    dropped: list[tuple[int, list[int]]] = field(default_factory=list)
    journal: list[tuple[str, int]] = field(default_factory=list)  # writes to Odoo, in order
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    next_po_id: int = 900

    def __post_init__(self) -> None:
        MemoryRoundStore.__init__(self)

    # --- reads -------------------------------------------------------------------

    async def order(self, po_name: str) -> OrderRef | None:
        return self.orders.get(po_name)

    async def basket_for_po(self, po_name: str) -> list[BasketLine]:
        return list(self.baskets.get(po_name, []))

    async def basket_for_product(self, product_id: int, qty: float) -> list[BasketLine]:
        if product_id not in self.products:
            return []
        return [
            BasketLine(
                product_id=product_id,
                product=self.products[product_id],
                qty=qty,
                last_paid=self.last_paid_by_product.get(product_id),
            )
        ]

    async def options_for(self, product_ids: list[int]) -> list[SupplierOption]:
        wanted = set(product_ids)
        out = []
        for option in self.options:
            listed = [e.product_id for e in self.entries if e.partner_id == option.partner_id]
            mine = sorted(set(listed) & wanted) if self.entries else sorted(wanted)
            out.append(option.model_copy(update={"product_ids": mine}))
        return out

    async def price_entries(self, product_ids: list[int]) -> list[PriceEntry]:
        return [e for e in self.entries if e.product_id in set(product_ids)]

    async def last_paid(self, product_id: int) -> float | None:
        return self.last_paid_by_product.get(product_id)

    async def read_rfq(self, po_id: int) -> RfqSnapshot | None:
        return self.rfqs.get(po_id)

    # --- writes ------------------------------------------------------------------

    async def create_rfq(
        self, partner_id: int, lines: list[BasketLine], *, external_ref: str, origin: str
    ) -> tuple[int, str]:
        for made in self.created_rfqs:
            if made["external_ref"] == external_ref:
                return made["po_id"], made["po_name"]
        self.next_po_id += 1
        po_id, po_name = self.next_po_id, f"P{self.next_po_id:05d}"
        self.created_rfqs.append(
            {
                "po_id": po_id,
                "po_name": po_name,
                "partner_id": partner_id,
                "lines": [line.model_dump(mode="json") for line in lines],
                "external_ref": external_ref,
                "origin": origin,
            }
        )
        option = next((o for o in self.options if o.partner_id == partner_id), None)
        self.rfqs[po_id] = RfqSnapshot(
            po_id=po_id,
            po_name=po_name,
            partner_id=partner_id,
            partner_name=option.partner_name if option else f"partner {partner_id}",
            state="draft",
            currency="USD",
            lines=[
                QuoteLine(
                    product_id=line.product_id,
                    product=line.product,
                    qty=line.qty,
                    line_id=po_id * 10 + i,
                )
                for i, line in enumerate(lines)
            ],
        )
        return po_id, po_name

    async def group_alternatives(self, po_ids: list[int]) -> int | None:
        if len(po_ids) < 2:
            return None
        self.groups.append(list(po_ids))
        return len(self.groups)

    async def confirm_rfq(self, po_id: int) -> str:
        self.confirmed.append(po_id)
        self.journal.append(("confirm", po_id))
        snap = self.rfqs.get(po_id)
        if snap is not None:
            self.rfqs[po_id] = snap.model_copy(update={"state": "purchase"})
        return snap.po_name if snap else f"P{po_id:05d}"

    async def cancel_rfq(self, po_id: int) -> None:
        self.cancelled.append(po_id)
        self.journal.append(("cancel", po_id))

    async def post_note(self, po_id: int, html: str) -> None:
        self.notes.append((po_id, html))

    async def drop_lines(self, po_id: int, keep_product_ids: list[int]) -> int:
        self.dropped.append((po_id, list(keep_product_ids)))
        self.journal.append(("drop", po_id))
        snap = self.rfqs.get(po_id)
        if snap is None:
            return 0
        kept = [ln for ln in snap.lines if ln.product_id in set(keep_product_ids)]
        self.rfqs[po_id] = snap.model_copy(update={"lines": kept})
        return len(snap.lines) - len(kept)

    # --- the supplier agent ------------------------------------------------------

    async def send_task(self, agent: str, task_json: str, *, case_id: str) -> AgentReply:
        self.sent_tasks.append({**json.loads(task_json), "agent": agent, "sent_for": case_id})
        if not self.supplier_replies:
            raise AssertionError("FakeSourcingPorts has no supplier reply left")
        item = self.supplier_replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    # --- runs --------------------------------------------------------------------

    async def start_run(
        self, *, run_id: str, case_id: str, model: str | None, trace_url: str | None
    ) -> None:
        self.runs[run_id] = {"case_id": case_id, "status": "running", "model": model}

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None:
        self.runs.setdefault(run_id, {})
        self.runs[run_id].update({"status": status, "summary": summary, "usage": usage})

    # --- helpers for tests -------------------------------------------------------

    def supplier_replied(self, po_id: int, price: float, *, lead_days: int | None = None) -> None:
        """The supplier answered: the quoted price is on the RFQ line, the mail is linked."""
        snap = self.rfqs[po_id]
        self.rfqs[po_id] = snap.model_copy(
            update={
                "lines": [
                    line.model_copy(update={"price_unit": price, "lead_days": lead_days})
                    for line in snap.lines
                ],
                "replied_at": datetime.now(UTC),
                "sent_at": snap.sent_at or datetime.now(UTC),
            }
        )


class BrokenSupplierAgent(ScError):
    pass


__all__ = [
    "ALTERNA",
    "HIDRAULICA",
    "IMPORTADORA",
    "VALVE",
    "BrokenSupplierAgent",
    "FakeSourcingPorts",
    "demo_entries",
    "demo_options",
    "supplier_reply",
]
