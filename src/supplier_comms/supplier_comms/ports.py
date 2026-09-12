"""Everything the graph needs from Odoo, Graph and the app database, behind one protocol.

``LivePorts`` composes the phase 1 repositories, the phase 2 mail client and
the outbound store. Tests use ``supplier_comms.testing.FakePorts``. Reads
are free; the only writes are the ones an approved decision unlocks
(``set_line_date``, ``upsert_price``, ``set_eta_meta``, the send).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol, cast

from sc_core.mail import normalize, pdf, po_token
from sc_core.mail.models import Attachment, MessageIds, OutboundMessage
from sc_core.mail.outbound import OutboundMailStore
from sc_core.mail.protocol import MailClient
from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import RunStatus
from sc_core.odoo.repositories import (
    AgentRunRepo,
    MailLinkRepo,
    PartnerRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
)
from sc_core.shared.time import utc_now
from supplier_comms import AGENT_NAME
from supplier_comms.models import InboundMeta, LineView, PoContext


class AgentPorts(Protocol):
    # --- reads -------------------------------------------------------------------
    async def load_po(self, po_name: str) -> PoContext | None: ...

    async def price_history(
        self, partner_id: int, product_id: int | None
    ) -> list[dict[str, Any]]: ...

    async def open_pos(self, partner_id: int) -> list[dict[str, Any]]: ...

    async def inbound_text(self, message_id: str) -> str: ...

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]: ...

    async def inbound_meta(self, message_id: str) -> InboundMeta: ...

    async def partner_by_email(self, address: str) -> int | None: ...

    # --- mail ----------------------------------------------------------------------
    async def create_draft(self, message: OutboundMessage) -> MessageIds: ...

    async def reply_draft(
        self,
        message_id: str,
        html_body: str,
        *,
        headers: dict[str, str],
        attachments: Sequence[Attachment] = (),
    ) -> MessageIds: ...

    async def report_pdf(self, po_id: int) -> bytes: ...

    async def send_draft(self, draft_id: str) -> None: ...

    async def find_sent(self, internet_message_id: str) -> MessageIds | None: ...

    async def record_outbound(self, *, ids: MessageIds, po_name: str, case_id: str) -> None: ...

    async def link_outbound(self, po_id: int, ids: MessageIds, *, case_id: str) -> None: ...

    async def link_inbound(self, po_id: int, meta: InboundMeta, *, case_id: str) -> None: ...

    # --- writes to Odoo (after approval only) -------------------------------------
    async def post_note(self, po_id: int, html: str) -> None: ...

    async def set_line_date(self, line_id: int, new_date: date, *, run_id: str) -> None: ...

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
    ) -> None: ...

    async def set_eta_meta(self, po_id: int, *, confidence: float) -> None: ...

    # --- run log (sc.agent.run) ---------------------------------------------------
    async def start_run(
        self,
        *,
        run_id: str,
        case_id: str,
        po_id: int | None,
        model: str | None,
        trace_url: str | None,
    ) -> None: ...

    async def finish_run(self, run_id: str, *, status: str, summary: str) -> None: ...


class LivePorts:
    def __init__(
        self,
        *,
        odoo: OdooClient,
        purchase_orders: PurchaseOrderRepo,
        partners: PartnerRepo,
        mail_links: MailLinkRepo,
        supplier_info: SupplierInfoRepo,
        agent_runs: AgentRunRepo,
        graph: MailClient,
        outbound: OutboundMailStore,
        pdf_max_pages: int = 20,
        pdf_max_bytes: int = 10_000_000,
    ) -> None:
        self._odoo = odoo
        self._runs = agent_runs
        self._pos = purchase_orders
        self._partners = partners
        self._links = mail_links
        self._supplier_info = supplier_info
        self._graph = graph
        self._outbound = outbound
        self._pdf_max_pages = pdf_max_pages
        self._pdf_max_bytes = pdf_max_bytes

    # --- reads -------------------------------------------------------------------

    async def load_po(self, po_name: str) -> PoContext | None:
        po = await self._pos.get_by_name(po_name)
        if po is None:
            return None
        lines = await self._pos.lines(po.id)
        partner = await self._partners.get(po.partner_id.id)
        company = await self._partners.commercial_partner(partner)
        emails = await self._partners.emails_of(company.id)
        if partner.email_normalized and partner.email_normalized not in emails:
            emails.insert(0, partner.email_normalized)
        links = await self._links.for_po(po.id)
        templates = await self._templates_of([ln.product_id.id for ln in lines if ln.product_id])
        currency = po.currency_id.name if po.currency_id else None
        return PoContext(
            id=po.id,
            name=po.name,
            state=po.state,
            partner_id=company.id,
            partner_name=company.name,
            partner_lang=partner.lang or company.lang,
            supplier_emails=emails,
            currency=currency,
            currency_id=po.currency_id.id if po.currency_id else None,
            date_planned=po.date_planned.date() if po.date_planned else None,
            amount_total=po.amount_total,
            lines=[
                LineView(
                    id=line.id,
                    product=line.product_id.name if line.product_id else line.name,
                    product_id=line.product_id.id if line.product_id else None,
                    product_tmpl_id=templates.get(line.product_id.id) if line.product_id else None,
                    qty=line.product_qty,
                    uom=line.product_uom.name if line.product_uom else None,
                    price_unit=line.price_unit,
                    currency=line.currency_id.name if line.currency_id else currency,
                    date_planned=line.date_planned.date() if line.date_planned else None,
                    qty_received=line.qty_received,
                )
                for line in lines
            ],
            prior_mail_links=len(links),
        )

    async def _templates_of(self, product_ids: list[int]) -> dict[int, int]:
        if not product_ids:
            return {}
        rows = await self._odoo.read("product.product", product_ids, ["product_tmpl_id"])
        out: dict[int, int] = {}
        for row in rows:
            tmpl = row.get("product_tmpl_id")
            if isinstance(tmpl, list | tuple) and tmpl:
                out[int(row["id"])] = int(tmpl[0])
        return out

    async def price_history(self, partner_id: int, product_id: int | None) -> list[dict[str, Any]]:
        rows = await self._supplier_info.for_partner(partner_id)
        out = []
        for row in rows:
            if product_id is not None and (
                row.product_id is None or row.product_id.id != product_id
            ):
                continue
            out.append(
                {
                    "product": row.product_name
                    or (row.product_id.name if row.product_id else None),
                    "price": row.price,
                    "currency": row.currency_id.name if row.currency_id else None,
                    "min_qty": row.min_qty,
                    "lead_days": row.delay,
                    "valid_from": row.date_start.isoformat() if row.date_start else None,
                }
            )
        return out

    async def open_pos(self, partner_id: int) -> list[dict[str, Any]]:
        orders = await self._pos.open_for_partner(partner_id)
        return [
            {
                "name": po.name,
                "state": po.state,
                "date_planned": po.date_planned.date().isoformat() if po.date_planned else None,
                "amount_total": po.amount_total,
            }
            for po in orders
        ]

    async def inbound_text(self, message_id: str) -> str:
        return normalize.text(await self._graph.get_body_text(message_id), html_body=False)

    async def attachments_text(self, message_id: str, *, max_chars: int) -> list[str]:
        texts: list[str] = []
        for attachment in await self._graph.attachments(message_id):
            if attachment.content_type != "application/pdf":
                continue
            text = pdf.extract_text(
                attachment.data, max_pages=self._pdf_max_pages, max_bytes=self._pdf_max_bytes
            )
            if text:
                texts.append(f"[{attachment.name}]\n{text[:max_chars]}")
        return texts

    # --- mail ----------------------------------------------------------------------

    async def create_draft(self, message: OutboundMessage) -> MessageIds:
        return await self._graph.create_draft(message)

    async def reply_draft(
        self,
        message_id: str,
        html_body: str,
        *,
        headers: dict[str, str],
        attachments: Sequence[Attachment] = (),
    ) -> MessageIds:
        return await self._graph.reply_draft(
            message_id, html_body, headers=headers, attachments=attachments
        )

    async def report_pdf(self, po_id: int) -> bytes:
        return await self._pos.report_pdf(po_id)

    async def send_draft(self, draft_id: str) -> None:
        await self._graph.send_draft(draft_id)

    async def find_sent(self, internet_message_id: str) -> MessageIds | None:
        return await self._graph.find_sent(internet_message_id)

    async def record_outbound(self, *, ids: MessageIds, po_name: str, case_id: str) -> None:
        await self._outbound.record(
            graph_message_id=ids.id,
            internet_message_id=ids.internet_message_id,
            conversation_id=ids.conversation_id,
            po_name=po_name,
            case_id=case_id,
        )

    async def link_outbound(self, po_id: int, ids: MessageIds, *, case_id: str) -> None:
        await self._links.link(
            po_id=po_id,
            graph_message_id=ids.id,
            direction="out",
            conversation_id=ids.conversation_id,
            internet_message_id=ids.internet_message_id,
            received_at=utc_now(),
            web_link=ids.web_link,
            case_id=case_id,
            confidence="exact",
        )

    # --- writes to Odoo -----------------------------------------------------------

    async def post_note(self, po_id: int, html: str) -> None:
        await self._pos.post_note(po_id, html)

    async def set_line_date(self, line_id: int, new_date: date, *, run_id: str) -> None:
        when = datetime(new_date.year, new_date.month, new_date.day, 12, 0, tzinfo=UTC)
        await self._pos.set_line_date_planned(line_id, when, source="supplier", run_id=run_id)

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
        await self._supplier_info.upsert_price(
            partner_id=partner_id,
            product_tmpl_id=product_tmpl_id,
            product_id=product_id,
            price=price,
            currency_id=currency_id,
            min_qty=min_qty,
            delay=lead_days,
        )

    async def set_eta_meta(self, po_id: int, *, confidence: float) -> None:
        await self._pos.set_eta_meta(po_id, source="supplier", confidence=confidence)

    # --- run log ------------------------------------------------------------------

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

    async def finish_run(self, run_id: str, *, status: str, summary: str) -> None:
        await self._runs.finish(run_id, cast(RunStatus, status), summary[:500])

    # --- unlinked mail ------------------------------------------------------------

    async def inbound_meta(self, message_id: str) -> InboundMeta:
        message = await self._graph.get_message(message_id)
        return InboundMeta(
            graph_message_id=message.id,
            sender_address=message.sender.normalized if message.sender else None,
            sender_name=message.sender.name if message.sender else None,
            subject_token=po_token.parse(message.subject),
            has_attachments=message.has_attachments,
            web_link=message.web_link,
            conversation_id=message.conversation_id,
            internet_message_id=message.internet_message_id,
        )

    async def partner_by_email(self, address: str) -> int | None:
        partner = await self._partners.find_by_email(address)
        if partner is None:
            return None
        return (await self._partners.commercial_partner(partner)).id

    async def link_inbound(self, po_id: int, meta: InboundMeta, *, case_id: str) -> None:
        await self._links.link(
            po_id=po_id,
            graph_message_id=meta.graph_message_id,
            direction="in",
            conversation_id=meta.conversation_id,
            internet_message_id=meta.internet_message_id,
            received_at=utc_now(),
            web_link=meta.web_link,
            case_id=case_id,
            confidence="agent",
        )
