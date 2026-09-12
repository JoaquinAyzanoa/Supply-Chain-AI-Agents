"""Rule-based linking of an inbound message to a purchase order.

Rules, in order; the first match wins:

1. ``x-sc-po`` internet header (set by our own outbound mail).
2. Subject token ``[P00015]``.
3. The conversation already has a link.
4. ``In-Reply-To`` / ``References`` names a Message-ID we sent.
5. The sender belongs to a supplier with exactly one open order.
6. No link: a hint (partner, open orders) for the supplier agent.

Rules 1 to 4 are exact. Rule 5 is a heuristic and is labelled as such so the
agent can double-check against the body. The linker never reads the body.
"""

from __future__ import annotations

import re
from typing import Literal, Protocol

from sc_core.mail import po_token
from sc_core.mail.models import InboundMessage
from sc_core.schema.base import StrictModel

Confidence = Literal["exact", "sender_single_open_po"]
Rule = Literal["header", "subject_token", "conversation", "in_reply_to", "sender_single_open_po"]

_MESSAGE_ID = re.compile(r"<[^<>\s]+>")


class PoRef(StrictModel):
    id: int
    name: str


class LinkResult(StrictModel):
    po: PoRef
    confidence: Confidence
    rule: Rule


class LinkHint(StrictModel):
    """What the agent gets when no rule matched."""

    partner_id: int | None = None
    open_po_names: list[str] = []


class LinkOutcome(StrictModel):
    link: LinkResult | None = None
    hint: LinkHint = LinkHint()


class LinkerPorts(Protocol):
    """Lookups the linker needs; Odoo-backed in production, in-memory in tests."""

    async def po_by_name(self, name: str) -> PoRef | None: ...

    async def po_by_conversation(self, conversation_id: str) -> PoRef | None: ...

    async def po_by_internet_message_id(self, internet_message_id: str) -> PoRef | None: ...

    async def partner_by_email(self, address: str) -> int | None:
        """Commercial partner (company) id for a contact address, if known."""
        ...

    async def open_pos_for_partner(self, partner_id: int) -> list[PoRef]: ...


def referenced_message_ids(headers: dict[str, str]) -> list[str]:
    """Message-IDs from ``In-Reply-To`` then ``References`` (newest first), de-duplicated."""
    ids: list[str] = []
    for name in ("in-reply-to", "references"):
        found = _MESSAGE_ID.findall(headers.get(name, ""))
        if name == "references":
            found.reverse()
        for mid in found:
            if mid not in ids:
                ids.append(mid)
    return ids


class Linker:
    def __init__(self, ports: LinkerPorts) -> None:
        self._ports = ports

    async def link(self, message: InboundMessage, headers: dict[str, str]) -> LinkOutcome:
        lowered = {k.lower(): v for k, v in headers.items()}

        # 1. our own header survives subject edits
        header_po = lowered.get(po_token.HEADER_PO, "").strip().upper()
        if header_po and (po := await self._ports.po_by_name(header_po)):
            return LinkOutcome(link=LinkResult(po=po, confidence="exact", rule="header"))

        # 2. subject token
        token = po_token.parse(message.subject)
        if token and (po := await self._ports.po_by_name(token)):
            return LinkOutcome(link=LinkResult(po=po, confidence="exact", rule="subject_token"))

        # 3. thread already linked
        if message.conversation_id and (
            po := await self._ports.po_by_conversation(message.conversation_id)
        ):
            return LinkOutcome(link=LinkResult(po=po, confidence="exact", rule="conversation"))

        # 4. reply to something we sent
        for mid in referenced_message_ids(lowered):
            if po := await self._ports.po_by_internet_message_id(mid):
                return LinkOutcome(link=LinkResult(po=po, confidence="exact", rule="in_reply_to"))

        # 5. known supplier with exactly one open order
        partner_id = None
        open_pos: list[PoRef] = []
        if message.sender is not None:
            partner_id = await self._ports.partner_by_email(message.sender.normalized)
            if partner_id is not None:
                open_pos = await self._ports.open_pos_for_partner(partner_id)
                if len(open_pos) == 1:
                    return LinkOutcome(
                        link=LinkResult(
                            po=open_pos[0],
                            confidence="sender_single_open_po",
                            rule="sender_single_open_po",
                        ),
                        hint=LinkHint(partner_id=partner_id, open_po_names=[open_pos[0].name]),
                    )

        # 6. hand over to the agent
        return LinkOutcome(
            hint=LinkHint(partner_id=partner_id, open_po_names=[po.name for po in open_pos])
        )
