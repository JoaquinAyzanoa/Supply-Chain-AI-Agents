"""In-memory ports for graph tests: an order, a mailbox and the records the nodes write."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import count
from typing import Any

from sc_core.mail.models import MessageIds, OutboundMessage
from supplier_comms.models import LineView, PoContext

SUPPLIER_EMAIL = "ventas.hidraulica.sc@gmail.com"


def demo_context(
    *, emails: list[str] | None = None, partner_id: int = 42, name: str = "P00015"
) -> PoContext:
    return PoContext(
        id=7,
        name=name,
        state="purchase",
        partner_id=partner_id,
        partner_name="Proveedor Hidraulica",
        supplier_emails=[SUPPLIER_EMAIL] if emails is None else emails,
        currency="PEN",
        currency_id=3,
        date_planned=date(2026, 10, 1),
        amount_total=1250.0,
        lines=[
            LineView(
                id=31,
                product="Bomba hidráulica 2HP",
                product_id=101,
                product_tmpl_id=1001,
                qty=2,
                uom="Unidades",
                price_unit=500.0,
                currency="PEN",
                date_planned=date(2026, 10, 1),
            ),
            LineView(
                id=32,
                product='Manguera 1/2"',
                product_id=102,
                product_tmpl_id=1002,
                qty=20,
                uom="m",
                price_unit=12.5,
                currency="PEN",
                date_planned=date(2026, 10, 1),
            ),
        ],
        prior_mail_links=1,
    )


@dataclass
class FakePorts:
    contexts: dict[str, PoContext] = field(default_factory=dict)
    prices: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    drafts: dict[str, OutboundMessage | dict[str, Any]] = field(default_factory=dict)
    sent_ids: list[str] = field(default_factory=list)
    outbound_records: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    notes: list[tuple[int, str]] = field(default_factory=list)
    find_sent_misses: int = 0
    inbound: dict[str, str] = field(default_factory=dict)
    attachments: dict[str, list[str]] = field(default_factory=dict)
    date_changes: list[dict[str, Any]] = field(default_factory=list)
    price_upserts: list[dict[str, Any]] = field(default_factory=list)
    eta_meta: list[dict[str, Any]] = field(default_factory=list)
    _seq: Any = field(default_factory=lambda: count(1))

    async def load_po(self, po_name: str) -> PoContext | None:
        return self.contexts.get(po_name)

    async def price_history(self, partner_id: int, product_id: int | None) -> list[dict[str, Any]]:
        return [p for p in self.prices if product_id is None or p.get("product_id") == product_id]

    async def open_pos(self, partner_id: int) -> list[dict[str, Any]]:
        return list(self.open_orders)

    def _ids(self, kind: str) -> MessageIds:
        n = next(self._seq)
        return MessageIds(
            id=f"{kind}{n}",
            internet_message_id=f"<msg{n}@outlook.fake>",
            conversation_id=f"conv{n}",
            web_link=f"https://outlook.live.com/fake/{kind}{n}",
        )

    async def create_draft(self, message: OutboundMessage) -> MessageIds:
        ids = self._ids("draft")
        self.drafts[ids.id] = message
        return ids

    async def reply_draft(
        self, message_id: str, html_body: str, *, headers: dict[str, str]
    ) -> MessageIds:
        ids = self._ids("reply")
        self.drafts[ids.id] = {"reply_to": message_id, "html_body": html_body, "headers": headers}
        return ids

    async def send_draft(self, draft_id: str) -> None:
        if draft_id not in self.drafts:
            raise AssertionError(f"unknown draft {draft_id}")
        self.sent_ids.append(draft_id)

    async def find_sent(self, internet_message_id: str) -> MessageIds | None:
        if self.find_sent_misses > 0:
            self.find_sent_misses -= 1
            return None
        n = internet_message_id.removeprefix("<msg").split("@")[0]
        return MessageIds(
            id=f"sent{n}",
            internet_message_id=internet_message_id,
            conversation_id=f"conv{n}",
            web_link=f"https://outlook.live.com/fake/sent{n}",
        )

    async def record_outbound(self, *, ids: MessageIds, po_name: str, case_id: str) -> None:
        self.outbound_records.append(
            {
                "graph_message_id": ids.id,
                "internet_message_id": ids.internet_message_id,
                "po_name": po_name,
                "case_id": case_id,
            }
        )

    async def link_outbound(self, po_id: int, ids: MessageIds, *, case_id: str) -> None:
        self.links.append(
            {"po_id": po_id, "graph_message_id": ids.id, "direction": "out", "case_id": case_id}
        )

    async def post_note(self, po_id: int, html: str) -> None:
        self.notes.append((po_id, html))

    async def inbound_text(self, message_id: str) -> str:
        return self.inbound.get(message_id, "")

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]:
        return [t[:max_chars] for t in self.attachments.get(message_id, [])]

    async def set_line_date(self, line_id: int, new_date: date, *, run_id: str) -> None:
        self.date_changes.append(
            {"line_id": line_id, "date": new_date.isoformat(), "run_id": run_id}
        )

    async def upsert_price(
        self,
        *,
        partner_id: int,
        product_tmpl_id: int,
        product_id: int | None,
        price: float,
        currency_id: int,
        min_qty: float,
        lead_days: int | None,
    ) -> None:
        self.price_upserts.append(
            {
                "partner_id": partner_id,
                "product_tmpl_id": product_tmpl_id,
                "product_id": product_id,
                "price": price,
                "currency_id": currency_id,
                "min_qty": min_qty,
                "lead_days": lead_days,
            }
        )

    async def set_eta_meta(self, po_id: int, *, confidence: float) -> None:
        self.eta_meta.append({"po_id": po_id, "confidence": confidence})
