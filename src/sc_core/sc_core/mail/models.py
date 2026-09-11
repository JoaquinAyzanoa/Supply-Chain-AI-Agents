"""Message models: what the rest of the system sees instead of Graph JSON.

``InboundMessage`` carries subject and sender because linking and
classification need them, but it is an in-memory object only: nothing here
is ever written to a database or a log. Persist identifiers (``id``,
``conversation_id``, ``internet_message_id``) and ``web_link`` only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from sc_core.schema.base import StrictModel
from sc_core.shared.time import parse_iso


class EmailAddress(StrictModel):
    address: str
    name: str | None = None

    @property
    def normalized(self) -> str:
        return self.address.strip().lower()

    @property
    def domain(self) -> str | None:
        addr = self.normalized
        return addr.rsplit("@", 1)[1] if "@" in addr else None

    @classmethod
    def from_graph(cls, node: dict[str, Any] | None) -> EmailAddress | None:
        email = (node or {}).get("emailAddress") or {}
        address = email.get("address")
        return cls(address=address, name=email.get("name") or None) if address else None


class InboundMessage(StrictModel):
    id: str
    conversation_id: str | None = None
    internet_message_id: str | None = None
    subject: str | None = None
    sender: EmailAddress | None = None
    to: list[EmailAddress] = []
    received_at: datetime | None = None
    has_attachments: bool = False
    is_read: bool = False
    web_link: str | None = None

    @classmethod
    def from_graph(cls, node: dict[str, Any]) -> InboundMessage:
        received = node.get("receivedDateTime")
        return cls(
            id=node["id"],
            conversation_id=node.get("conversationId"),
            internet_message_id=node.get("internetMessageId"),
            subject=node.get("subject") or None,
            sender=EmailAddress.from_graph(node.get("from") or node.get("sender")),
            to=[
                a
                for a in (EmailAddress.from_graph(r) for r in node.get("toRecipients") or [])
                if a is not None
            ],
            received_at=parse_iso(received) if received else None,
            has_attachments=bool(node.get("hasAttachments")),
            is_read=bool(node.get("isRead")),
            web_link=node.get("webLink"),
        )


class DeltaPage(StrictModel):
    """Everything new since the previous delta link, plus the link for next time."""

    messages: list[InboundMessage]
    removed_ids: list[str] = []
    delta_link: str


class Attachment(StrictModel):
    name: str
    content_type: str
    size: int = Field(ge=0)
    data: bytes

    @property
    def is_pdf(self) -> bool:
        return self.content_type == "application/pdf" or self.name.lower().endswith(".pdf")


class MessageIds(StrictModel):
    """What the system keeps about a message it created: never the content."""

    id: str
    internet_message_id: str | None = None
    conversation_id: str | None = None
    web_link: str | None = None

    @classmethod
    def from_graph(cls, node: dict[str, Any]) -> MessageIds:
        return cls(
            id=node["id"],
            internet_message_id=node.get("internetMessageId"),
            conversation_id=node.get("conversationId"),
            web_link=node.get("webLink"),
        )


class OutboundMessage(StrictModel):
    """A message to create as a draft or send. ``headers`` must use ``x-`` names (Graph rule)."""

    to: list[str] = Field(min_length=1)
    subject: str = Field(min_length=1)
    html_body: str
    cc: list[str] = []
    headers: dict[str, str] = {}

    def tagged(self, po_name: str, case_id: str | None = None) -> OutboundMessage:
        """Copy with the order token in the subject and the tracking headers set."""
        from sc_core.mail import po_token

        return self.model_copy(
            update={
                "subject": po_token.tag_subject(self.subject, po_name),
                "headers": {**self.headers, **po_token.headers_for(po_name, case_id)},
            }
        )

    def to_graph(self) -> dict[str, Any]:
        node: dict[str, Any] = {
            "subject": self.subject,
            "body": {"contentType": "html", "content": self.html_body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in self.to],
        }
        if self.cc:
            node["ccRecipients"] = [{"emailAddress": {"address": a}} for a in self.cc]
        if self.headers:
            for name in self.headers:
                if not name.lower().startswith("x-"):
                    raise ValueError(f"custom internet headers must start with 'x-': {name!r}")
            node["internetMessageHeaders"] = [
                {"name": k, "value": v} for k, v in self.headers.items()
            ]
        return node
