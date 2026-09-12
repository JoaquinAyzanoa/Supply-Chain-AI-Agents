"""``FakeGraph``: an in-memory mailbox with the ``MailClient`` surface.

For unit tests of anything that reads or sends mail (mail_sync, the
supplier agent) without Graph. Behaviour mirrors what the integration tests
observed on the real service:

- delta links are cursors: ``inbox_delta(None)`` returns everything and a
  link; calling with that link returns only what arrived afterwards
- drafts get ids and a Message-ID at creation; sending moves the message to
  ``sent`` with a new id but the same Message-ID and conversation
- ``reply_draft`` inherits the conversation of the message replied to
- ``receive(...)`` is the test's hand: it drops a message in the inbox

Failures can be injected with ``fail_next``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import count
from typing import Any

from sc_core.mail.errors import GraphError
from sc_core.mail.models import (
    Attachment,
    DeltaPage,
    EmailAddress,
    InboundMessage,
    MessageIds,
    OutboundMessage,
)

_DELTA_PREFIX = "fake-delta:"


class FakeGraph:
    def __init__(self, address: str = "scai.compras@outlook.com", name: str = "Purchasing Team"):
        self.address, self.name = address, name
        self.inbox: list[InboundMessage] = []
        self.removed: list[str] = []
        self.bodies: dict[str, str] = {}
        self.headers: dict[str, dict[str, str]] = {}
        self.attachments_by_id: dict[str, list[Attachment]] = {}
        self.drafts: dict[str, OutboundMessage | dict[str, Any]] = {}
        self.draft_ids: dict[str, MessageIds] = {}
        self.sent: list[tuple[MessageIds, OutboundMessage | dict[str, Any]]] = []
        self.sent_immediately: list[OutboundMessage] = []
        self.deleted: list[str] = []
        self._seq = count(1)
        self._pending_failure: Exception | None = None

    # --- test controls ----------------------------------------------------------

    def fail_next(self, exc: Exception | None = None) -> None:
        """Make the next call raise ``exc`` (a retryable ``GraphError`` by default)."""
        self._pending_failure = exc or GraphError("injected failure")

    def receive(
        self,
        *,
        subject: str | None,
        sender: str,
        body: str = "",
        headers: dict[str, str] | None = None,
        conversation_id: str | None = None,
        internet_message_id: str | None = None,
        attachments: list[Attachment] | None = None,
        received_at: datetime | None = None,
        sender_name: str | None = None,
    ) -> InboundMessage:
        n = next(self._seq)
        message = InboundMessage(
            id=f"AAMk{n}",
            conversation_id=conversation_id or f"conv{n}",
            internet_message_id=internet_message_id or f"<msg{n}@fake>",
            subject=subject,
            sender=EmailAddress(address=sender, name=sender_name),
            to=[EmailAddress(address=self.address)],
            received_at=received_at or datetime.now(UTC),
            has_attachments=bool(attachments),
            web_link=f"https://outlook.live.com/fake/{n}",
        )
        self.inbox.append(message)
        self.bodies[message.id] = body
        self.headers[message.id] = {k.lower(): v for k, v in (headers or {}).items()}
        self.attachments_by_id[message.id] = list(attachments or [])
        return message

    def _check_failure(self) -> None:
        if self._pending_failure is not None:
            exc, self._pending_failure = self._pending_failure, None
            raise exc

    def _find(self, message_id: str) -> InboundMessage:
        for message in self.inbox:
            if message.id == message_id:
                return message
        raise GraphError("message not found", details={"status": 404}, retryable=False)

    # --- MailClient ---------------------------------------------------------------

    async def me(self) -> dict[str, Any]:
        self._check_failure()
        return {"displayName": self.name, "mail": self.address, "userPrincipalName": self.address}

    async def inbox_delta(self, delta_link: str | None, *, page_size: int = 50) -> DeltaPage:
        self._check_failure()
        start = int(delta_link[len(_DELTA_PREFIX) :]) if delta_link else 0
        end = len(self.inbox)
        removed = self.removed[start:end] if delta_link else []
        return DeltaPage(
            messages=list(self.inbox[start:end]),
            removed_ids=removed,
            delta_link=f"{_DELTA_PREFIX}{end}",
        )

    async def get_message(self, message_id: str) -> InboundMessage:
        self._check_failure()
        return self._find(message_id)

    async def get_body_text(self, message_id: str) -> str:
        self._check_failure()
        self._find(message_id)
        return self.bodies.get(message_id, "")

    async def get_headers(self, message_id: str) -> dict[str, str]:
        self._check_failure()
        self._find(message_id)
        return dict(self.headers.get(message_id, {}))

    async def attachments(self, message_id: str) -> list[Attachment]:
        self._check_failure()
        self._find(message_id)
        return list(self.attachments_by_id.get(message_id, []))

    async def send(self, message: OutboundMessage) -> None:
        self._check_failure()
        self.sent_immediately.append(message)

    async def create_draft(self, message: OutboundMessage) -> MessageIds:
        self._check_failure()
        n = next(self._seq)
        ids = MessageIds(
            id=f"draft{n}",
            internet_message_id=f"<draft{n}@fake>",
            conversation_id=f"conv{n}",
            web_link=f"https://outlook.live.com/fake/draft{n}",
        )
        self.drafts[ids.id] = message
        self.draft_ids[ids.id] = ids
        return ids

    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, html_body: str | None = None
    ) -> None:
        self._check_failure()
        if draft_id not in self.drafts:
            raise GraphError("draft not found", details={"status": 404}, retryable=False)
        message = self.drafts[draft_id]
        if not isinstance(message, OutboundMessage):
            return  # a reply draft: only its body is tracked, nothing to update
        self.drafts[draft_id] = message.model_copy(
            update={
                "subject": subject if subject is not None else message.subject,
                "html_body": html_body if html_body is not None else message.html_body,
            }
        )

    async def send_draft(self, draft_id: str) -> None:
        self._check_failure()
        if draft_id not in self.drafts:
            raise GraphError("draft not found", details={"status": 404}, retryable=False)
        ids = self.draft_ids.pop(draft_id)
        message = self.drafts.pop(draft_id)
        sent_ids = ids.model_copy(
            update={
                "id": f"sent{next(self._seq)}",
                "web_link": f"https://outlook.live.com/fake/{ids.id}",
            }
        )
        self.sent.append((sent_ids, message))

    async def reply_draft(
        self,
        message_id: str,
        html_body: str,
        *,
        headers: dict[str, str] | None = None,
        attachments: Sequence[Attachment] = (),
    ) -> MessageIds:
        self._check_failure()
        original = self._find(message_id)
        n = next(self._seq)
        ids = MessageIds(
            id=f"draft{n}",
            internet_message_id=f"<reply{n}@fake>",
            conversation_id=original.conversation_id,
            web_link=f"https://outlook.live.com/fake/draft{n}",
        )
        self.drafts[ids.id] = {
            "reply_to": message_id,
            "html_body": html_body,
            "headers": headers or {},
            "attachments": list(attachments),
        }
        self.draft_ids[ids.id] = ids
        return ids

    async def find_sent(self, internet_message_id: str) -> MessageIds | None:
        self._check_failure()
        for ids, _message in self.sent:
            if ids.internet_message_id == internet_message_id:
                return ids
        return None

    async def send_tracked(
        self, message: OutboundMessage, *, lookup_attempts: int = 5, lookup_delay: float = 2.0
    ) -> MessageIds:
        draft = await self.create_draft(message)
        await self.send_draft(draft.id)
        found = await self.find_sent(draft.internet_message_id or "")
        assert found is not None
        return found

    async def delete_message(self, message_id: str) -> None:
        self._check_failure()
        self.deleted.append(message_id)
        self.drafts.pop(message_id, None)
        self.draft_ids.pop(message_id, None)
        before = len(self.inbox)
        self.inbox = [m for m in self.inbox if m.id != message_id]
        if len(self.inbox) != before:
            self.removed.append(message_id)
