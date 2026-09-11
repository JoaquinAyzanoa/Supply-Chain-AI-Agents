"""Links between Outlook messages and purchase orders (``sc.mail.link``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sc_core.odoo.models import LinkConfidence, MailDirection, MailLink, to_odoo_datetime
from sc_core.odoo.repositories.base import Repo


class MailLinkRepo(Repo[MailLink]):
    model = MailLink

    async def link(
        self,
        *,
        po_id: int,
        graph_message_id: str,
        direction: MailDirection,
        conversation_id: str | None = None,
        internet_message_id: str | None = None,
        received_at: datetime | None = None,
        web_link: str | None = None,
        case_id: str | None = None,
        confidence: LinkConfidence = "exact",
    ) -> MailLink:
        """Create the link. Idempotent: an existing link for the message is returned as is."""
        if existing := await self.find_by_message(graph_message_id):
            return existing
        values: dict[str, Any] = {
            "po_id": po_id,
            "graph_message_id": graph_message_id,
            "direction": direction,
            "confidence": confidence,
        }
        if conversation_id:
            values["graph_conversation_id"] = conversation_id
        if internet_message_id:
            values["internet_message_id"] = internet_message_id
        if received_at is not None:
            values["received_at"] = to_odoo_datetime(received_at)
        if web_link:
            values["web_link"] = web_link
        if case_id:
            values["case_id"] = case_id
        new_id = await self._c.create(self._name, values)
        return await self.get(new_id)

    async def find_by_message(self, graph_message_id: str) -> MailLink | None:
        return await self.find_one([["graph_message_id", "=", graph_message_id]])

    async def find_by_conversation(self, conversation_id: str) -> MailLink | None:
        """Most recent link in a conversation, which decides the order for replies."""
        return await self.find_one(
            [["graph_conversation_id", "=", conversation_id]], order="received_at desc, id desc"
        )

    async def find_by_internet_message_id(self, internet_message_id: str) -> MailLink | None:
        return await self.find_one([["internet_message_id", "=", internet_message_id]])

    async def for_po(self, po_id: int) -> list[MailLink]:
        return await self.find([["po_id", "=", po_id]], order="received_at desc, id desc")
