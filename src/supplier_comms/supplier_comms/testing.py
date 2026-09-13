"""In-memory ports for graph tests: an order, a mailbox and the records the nodes write."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import count
from typing import Any

from sc_core.infra.internal_requests import InternalRequest
from sc_core.mail.models import Attachment, MessageIds, OutboundMessage
from sc_core.mail.tables import TableData
from sc_core.schema.profiles import SupplierProfile
from supplier_comms.models import InboundMeta, LineView, PoContext

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
        partner_lang="es_PE",  # the demo supplier reads Spanish; the instance may be English
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
    updated_drafts: list[dict[str, Any]] = field(default_factory=list)
    outbound_records: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    notes: list[tuple[int, str]] = field(default_factory=list)
    find_sent_misses: int = 0
    inbound: dict[str, str] = field(default_factory=dict)
    metas: dict[str, InboundMeta] = field(default_factory=dict)
    partners: dict[str, int] = field(default_factory=dict)  # email -> commercial partner id
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pdf_bytes: bytes = b"%PDF-1.4 fake purchase order"
    attachments: dict[str, list[str]] = field(default_factory=dict)
    date_changes: list[dict[str, Any]] = field(default_factory=list)
    splits: list[dict[str, Any]] = field(default_factory=list)
    rfqs_marked_sent: list[int] = field(default_factory=list)
    price_upserts: list[dict[str, Any]] = field(default_factory=list)
    eta_meta: list[dict[str, Any]] = field(default_factory=list)
    profiles: dict[int, SupplierProfile] = field(default_factory=dict)
    products_by_code: dict[str, tuple[int, str]] = field(default_factory=dict)
    created_partners: list[dict[str, Any]] = field(default_factory=list)
    created_rfqs: list[dict[str, Any]] = field(default_factory=list)
    # phase 11 S6
    tables: dict[str, list[TableData]] = field(default_factory=dict)
    anchors: dict[int, str] = field(default_factory=dict)
    terms: dict[int, dict[str, Any]] = field(default_factory=dict)
    catalogue: list[dict[str, Any]] = field(default_factory=list)  # id, code, name
    reference_suppliers: dict[int, dict[str, Any]] = field(default_factory=dict)
    template_ids: dict[int, int] = field(default_factory=dict)
    currency_ids: dict[str, int] = field(default_factory=lambda: {"USD": 2, "PEN": 1})
    internal_requests: list[InternalRequest] = field(default_factory=list)
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
        self,
        message_id: str,
        html_body: str,
        *,
        headers: dict[str, str],
        attachments: Sequence[Attachment] = (),
    ) -> MessageIds:
        ids = self._ids("reply")
        self.drafts[ids.id] = {
            "reply_to": message_id,
            "html_body": html_body,
            "headers": headers,
            "attachments": list(attachments),
        }
        return ids

    async def report_pdf(self, po_id: int) -> bytes:
        return self.pdf_bytes

    async def send_draft(self, draft_id: str) -> None:
        if draft_id not in self.drafts:
            raise AssertionError(f"unknown draft {draft_id}")
        self.sent_ids.append(draft_id)

    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, html_body: str | None = None
    ) -> None:
        message = self.drafts[draft_id]
        if isinstance(message, OutboundMessage):
            self.drafts[draft_id] = message.model_copy(
                update={
                    "subject": subject if subject is not None else message.subject,
                    "html_body": html_body if html_body is not None else message.html_body,
                }
            )
        self.updated_drafts.append(
            {"draft_id": draft_id, "subject": subject, "html_body": html_body}
        )

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

    async def mark_rfq_sent(self, po_id: int) -> None:
        self.rfqs_marked_sent.append(po_id)

    async def post_note(self, po_id: int, html: str) -> None:
        self.notes.append((po_id, html))

    async def supplier_profile(self, partner_id: int) -> SupplierProfile | None:
        return self.profiles.get(partner_id)

    async def inbound_text(self, message_id: str) -> str:
        return self.inbound.get(message_id, "")

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]:
        return [t[:max_chars] for t in self.attachments.get(message_id, [])]

    async def set_line_date(self, line_id: int, new_date: date, *, run_id: str) -> None:
        self.date_changes.append(
            {"line_id": line_id, "date": new_date.isoformat(), "run_id": run_id}
        )

    async def split_line(
        self, line_id: int, parts: list[tuple[float, date]], *, run_id: str
    ) -> list[int]:
        self.splits.append(
            {
                "line_id": line_id,
                "parts": [(qty, d.isoformat()) for qty, d in parts],
                "run_id": run_id,
            }
        )
        return [9000 + len(self.splits) * 10 + i for i in range(1, len(parts))]

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

    async def start_run(self, **fields: Any) -> None:
        if fields["run_id"] not in self.runs:
            self.runs[fields["run_id"]] = {**fields, "status": "running"}

    async def finish_run(
        self, run_id: str, *, status: str, summary: str, usage: dict[str, Any] | None = None
    ) -> None:
        self.runs.setdefault(run_id, {"run_id": run_id})
        self.runs[run_id].update(status=status, summary=summary, usage=usage)

    async def inbound_meta(self, message_id: str) -> InboundMeta:
        return self.metas.get(message_id) or InboundMeta(graph_message_id=message_id)

    async def create_supplier(self, name: str, email: str | None) -> tuple[int, str]:
        partner_id = 500 + len(self.created_partners)
        self.created_partners.append({"id": partner_id, "name": name, "email": email})
        if email:
            self.partners[email.lower()] = partner_id
        return partner_id, name

    async def product_by_code(self, code: str) -> tuple[int, str] | None:
        return self.products_by_code.get(code.strip().upper())

    async def create_rfq(
        self, partner_id: int, lines: list[dict[str, Any]], *, external_ref: str, origin: str
    ) -> tuple[int, str]:
        po_id = 700 + len(self.created_rfqs)
        po_name = f"P{po_id:05d}"
        self.created_rfqs.append(
            {
                "po_id": po_id,
                "po_name": po_name,
                "partner_id": partner_id,
                "lines": lines,
                "external_ref": external_ref,
                "origin": origin,
            }
        )
        return po_id, po_name

    async def partner_by_email(self, address: str) -> int | None:
        return self.partners.get(address.lower())

    async def attachment_tables(self, message_id: str) -> list[TableData]:
        return list(self.tables.get(message_id, []))

    async def thread_anchor(self, po_id: int) -> str | None:
        return self.anchors.get(po_id)

    async def po_terms(self, po_id: int) -> dict[str, Any]:
        return dict(self.terms.get(po_id, {}))

    async def search_products(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        words = [w.lower() for w in query.split() if len(w) > 2]
        hits = [
            p for p in self.catalogue if any(w in str(p.get("name", "")).lower() for w in words)
        ]
        return hits[:limit]

    async def reference_supplier(self, product_id: int) -> dict[str, Any] | None:
        return self.reference_suppliers.get(product_id)

    async def product_template_id(self, product_id: int) -> int | None:
        return self.template_ids.get(product_id, product_id * 10)

    async def currency_id_for(self, code: str) -> int | None:
        return self.currency_ids.get(code.upper())

    async def save_internal_request(self, request: InternalRequest) -> InternalRequest:
        saved = request.model_copy(update={"id": len(self.internal_requests) + 1})
        self.internal_requests.append(saved)
        return saved

    async def internal_request_for(self, po_name: str) -> InternalRequest | None:
        for request in reversed(self.internal_requests):
            if po_name in request.po_names:
                return request
        return None

    async def finish_internal_request(self, request_id: int, status: str) -> None:
        self.internal_requests = [
            r.model_copy(update={"status": status}) if r.id == request_id else r
            for r in self.internal_requests
        ]

    async def link_inbound(self, po_id: int, meta: InboundMeta, *, case_id: str) -> None:
        self.links.append(
            {
                "po_id": po_id,
                "graph_message_id": meta.graph_message_id,
                "direction": "in",
                "case_id": case_id,
                "confidence": "agent",
            }
        )
