"""The mail client surface that services depend on.

``GraphMailClient`` (real) and ``FakeGraph`` (tests) both satisfy this
protocol structurally. Services and repositories type their dependency as
``MailClient`` so unit tests never need the network or a token.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from sc_core.mail.models import Attachment, DeltaPage, InboundMessage, MessageIds, OutboundMessage


class MailClient(Protocol):
    async def me(self) -> dict[str, Any]: ...

    async def inbox_delta(self, delta_link: str | None, *, page_size: int = 50) -> DeltaPage: ...

    async def get_message(self, message_id: str) -> InboundMessage: ...

    async def get_body_text(self, message_id: str) -> str: ...

    async def get_headers(self, message_id: str) -> dict[str, str]: ...

    async def attachments(self, message_id: str) -> list[Attachment]: ...

    async def send(self, message: OutboundMessage) -> None: ...

    async def create_draft(self, message: OutboundMessage) -> MessageIds: ...

    async def send_draft(self, draft_id: str) -> None: ...

    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, html_body: str | None = None
    ) -> None: ...

    async def reply_draft(
        self,
        message_id: str,
        html_body: str,
        *,
        headers: dict[str, str] | None = None,
        attachments: Sequence[Attachment] = (),
    ) -> MessageIds: ...

    async def find_sent(self, internet_message_id: str) -> MessageIds | None: ...

    async def send_tracked(
        self, message: OutboundMessage, *, lookup_attempts: int = 5, lookup_delay: float = 2.0
    ) -> MessageIds: ...

    async def delete_message(self, message_id: str) -> None: ...
