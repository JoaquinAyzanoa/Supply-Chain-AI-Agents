"""In-memory business system for mail_sync unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from mail_sync.linker import Confidence, PoRef
from sc_core.mail.models import InboundMessage


@dataclass
class RecordedLink:
    po: PoRef
    message_id: str
    case_id: str
    confidence: Confidence


@dataclass
class FakePorts:
    """Purchase orders, conversation links, sent Message-IDs and supplier contacts."""

    pos: dict[str, PoRef] = field(default_factory=dict)
    conversations: dict[str, str] = field(default_factory=dict)  # conversation id -> po name
    sent_message_ids: dict[str, str] = field(default_factory=dict)  # Message-ID -> po name
    contacts: dict[str, int] = field(default_factory=dict)  # email -> commercial partner id
    open_pos: dict[int, list[str]] = field(default_factory=dict)  # partner id -> po names
    links: list[RecordedLink] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def add_po(self, name: str, *, partner_id: int | None = None) -> PoRef:
        po = PoRef(id=len(self.pos) + 1, name=name)
        self.pos[name] = po
        if partner_id is not None:
            self.open_pos.setdefault(partner_id, []).append(name)
        return po

    async def po_by_name(self, name: str) -> PoRef | None:
        self.calls.append(f"po_by_name:{name}")
        return self.pos.get(name)

    async def po_by_conversation(self, conversation_id: str) -> PoRef | None:
        self.calls.append(f"po_by_conversation:{conversation_id}")
        name = self.conversations.get(conversation_id)
        return self.pos.get(name) if name else None

    async def po_by_internet_message_id(self, internet_message_id: str) -> PoRef | None:
        self.calls.append(f"po_by_internet_message_id:{internet_message_id}")
        name = self.sent_message_ids.get(internet_message_id)
        return self.pos.get(name) if name else None

    async def partner_by_email(self, address: str) -> int | None:
        self.calls.append(f"partner_by_email:{address}")
        return self.contacts.get(address)

    async def open_pos_for_partner(self, partner_id: int) -> list[PoRef]:
        self.calls.append(f"open_pos_for_partner:{partner_id}")
        return [self.pos[n] for n in self.open_pos.get(partner_id, [])]

    async def record_link(
        self, po: PoRef, message: InboundMessage, *, case_id: str, confidence: Confidence
    ) -> None:
        self.links.append(RecordedLink(po, message.id, case_id, confidence))
        if message.conversation_id:
            self.conversations[message.conversation_id] = po.name
