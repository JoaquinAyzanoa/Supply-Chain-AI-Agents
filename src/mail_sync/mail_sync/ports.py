"""Odoo-backed implementation of what the sync needs from the business system."""

from __future__ import annotations

from mail_sync.linker import Confidence, PoRef
from mail_sync.state import SyncState
from sc_core.mail.models import InboundMessage
from sc_core.odoo.repositories import MailLinkRepo, PartnerRepo, PurchaseOrderRepo


class OdooPorts:
    """Linker lookups plus link creation, all through the phase 1 repositories."""

    def __init__(
        self,
        *,
        purchase_orders: PurchaseOrderRepo,
        mail_links: MailLinkRepo,
        partners: PartnerRepo,
        state: SyncState,
    ) -> None:
        self._pos = purchase_orders
        self._links = mail_links
        self._partners = partners
        self._state = state

    async def po_by_name(self, name: str) -> PoRef | None:
        po = await self._pos.get_by_name(name)
        return PoRef(id=po.id, name=po.name) if po else None

    async def po_by_conversation(self, conversation_id: str) -> PoRef | None:
        link = await self._links.find_by_conversation(conversation_id)
        return PoRef(id=link.po_id.id, name=link.po_id.name) if link else None

    async def po_by_internet_message_id(self, internet_message_id: str) -> PoRef | None:
        link = await self._links.find_by_internet_message_id(internet_message_id)
        if link:
            return PoRef(id=link.po_id.id, name=link.po_id.name)
        po_name = await self._state.outbound_po(internet_message_id)
        return await self.po_by_name(po_name) if po_name else None

    async def partner_by_email(self, address: str) -> int | None:
        partner = await self._partners.find_by_email(address)
        if partner is None:
            return None
        company = await self._partners.commercial_partner(partner)
        return company.id

    async def open_pos_for_partner(self, partner_id: int) -> list[PoRef]:
        orders = await self._pos.open_for_partner(partner_id)
        return [PoRef(id=po.id, name=po.name) for po in orders]

    async def record_link(
        self, po: PoRef, message: InboundMessage, *, case_id: str, confidence: Confidence
    ) -> None:
        await self._links.link(
            po_id=po.id,
            graph_message_id=message.id,
            direction="in",
            conversation_id=message.conversation_id,
            internet_message_id=message.internet_message_id,
            received_at=message.received_at,
            web_link=message.web_link,
            case_id=case_id,
            confidence=confidence,
        )
