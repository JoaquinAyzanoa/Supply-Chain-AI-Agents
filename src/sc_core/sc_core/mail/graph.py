"""Microsoft Graph mail client: the only module that speaks Graph's HTTP API.

Read side (phase 2 story S3): delta queries over the inbox, message body as
text, internet headers, file attachments. Write side (story S4): send,
drafts, replies.

Retry policy, in ``_request``:

- 401 once: the access token may have just expired; force a refresh and
  retry a single time, then give up with ``MailAuthRequired``.
- 429: honour ``Retry-After`` (Graph always sends it), up to ``max_retries``.
- 5xx and transport errors: exponential backoff, up to ``max_retries``.
- 410 on a delta request: ``DeltaExpired`` so the caller resyncs from scratch.
- other 4xx: ``GraphError`` (not retryable) with Graph's error code.

Bodies and attachments are returned to the caller and never stored here.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from loguru import logger

from sc_core.mail.auth.base import TokenProvider
from sc_core.mail.errors import DeltaExpired, GraphError, MailAuthRequired
from sc_core.mail.models import (
    Attachment,
    DeltaPage,
    InboundMessage,
    MessageIds,
    OutboundMessage,
    attachment_node,
)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MESSAGE_SELECT = (
    "id,conversationId,internetMessageId,subject,from,sender,toRecipients,"
    "receivedDateTime,hasAttachments,webLink,isRead"
)
Sleep = Callable[[float], Awaitable[None]]

_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 30.0
_DEFAULT_RETRY_AFTER = 5.0


class GraphMailClient:
    def __init__(
        self,
        tokens: TokenProvider,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        max_retries: int = 3,
        timeout: float = 30.0,
        base_url: str = GRAPH_BASE,
    ) -> None:
        self._tokens = tokens
        self._sleep = sleep
        self._max_retries = max_retries
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> GraphMailClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- transport ------------------------------------------------------------

    def _mailbox(self) -> str:
        return f"{self._base}{self._tokens.mailbox_prefix()}"

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        refreshed = False
        attempt = 0
        while True:
            token = await self._tokens.access_token(force_refresh=refreshed and attempt == 0)
            request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
            try:
                response = await self._http.request(
                    method, url, json=json, headers=request_headers, params=params
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise GraphError(f"graph unreachable: {type(exc).__name__}") from exc
                await self._backoff(attempt, reason=type(exc).__name__)
                attempt += 1
                continue

            status = response.status_code
            if status < 400:
                return response
            if status == 401 and not refreshed:
                refreshed = True
                attempt = 0
                logger.warning("graph returned 401; refreshing the access token once")
                continue
            if status == 401:
                raise MailAuthRequired(
                    "graph rejected the token even after a refresh; run `just mail-login`"
                )
            if status == 410:
                raise DeltaExpired()
            if status == 429 or status >= 500:
                if attempt >= self._max_retries:
                    raise GraphError(
                        f"graph returned HTTP {status} after {attempt + 1} attempts",
                        details=_error_details(response),
                    )
                delay = _retry_after(response) if status == 429 else _backoff_delay(attempt)
                logger.bind(status=status, delay=delay).warning(
                    "graph call throttled/failed, retrying"
                )
                await self._sleep(delay)
                attempt += 1
                continue
            raise GraphError(
                f"graph returned HTTP {status}", details=_error_details(response), retryable=False
            )

    async def _backoff(self, attempt: int, *, reason: str) -> None:
        delay = _backoff_delay(attempt)
        logger.bind(delay=delay, reason=reason).warning("graph transport error, retrying")
        await self._sleep(delay)

    # --- identity ---------------------------------------------------------------

    async def me(self) -> dict[str, Any]:
        """Display name and address of the mailbox the tokens act on."""
        response = await self._request(
            "GET", f"{self._mailbox()}", params={"$select": "displayName,mail,userPrincipalName"}
        )
        data: dict[str, Any] = response.json()
        return data

    # --- reading ----------------------------------------------------------------

    async def inbox_delta(self, delta_link: str | None, *, page_size: int = 50) -> DeltaPage:
        """Changes in the inbox since ``delta_link`` (all of it when ``None``).

        Follows ``@odata.nextLink`` pages until Graph hands back an
        ``@odata.deltaLink``; that link is what the caller stores for next
        time. Removed entries are reported by id only.
        """
        if delta_link:
            url, params = delta_link, None
        else:
            url = f"{self._mailbox()}/mailFolders/inbox/messages/delta"
            params = {"$select": MESSAGE_SELECT, "$top": str(page_size)}
        messages: list[InboundMessage] = []
        removed: list[str] = []
        while True:
            body = (await self._request("GET", url, params=params)).json()
            params = None
            for node in body.get("value", []):
                if "@removed" in node:
                    removed.append(node["id"])
                else:
                    messages.append(InboundMessage.from_graph(node))
            if "@odata.nextLink" in body:
                url = body["@odata.nextLink"]
                continue
            if "@odata.deltaLink" not in body:
                raise GraphError("delta response without deltaLink or nextLink", retryable=False)
            return DeltaPage(
                messages=messages, removed_ids=removed, delta_link=body["@odata.deltaLink"]
            )

    async def get_message(self, message_id: str) -> InboundMessage:
        response = await self._request(
            "GET", f"{self._mailbox()}/messages/{message_id}", params={"$select": MESSAGE_SELECT}
        )
        return InboundMessage.from_graph(response.json())

    async def get_body_text(self, message_id: str) -> str:
        """Plain text of *this* message only (quoted history stripped by Graph's ``uniqueBody``)."""
        response = await self._request(
            "GET",
            f"{self._mailbox()}/messages/{message_id}",
            params={"$select": "body,uniqueBody"},
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        node = response.json()
        unique = (node.get("uniqueBody") or {}).get("content")
        full = (node.get("body") or {}).get("content")
        return str(unique if unique is not None else full or "")

    async def get_headers(self, message_id: str) -> dict[str, str]:
        """Internet headers with lower-cased names (``x-sc-po``, ``in-reply-to``, ...)."""
        response = await self._request(
            "GET",
            f"{self._mailbox()}/messages/{message_id}",
            params={"$select": "internetMessageHeaders"},
        )
        headers = response.json().get("internetMessageHeaders") or []
        return {h["name"].lower(): h["value"] for h in headers if "name" in h}

    async def attachments(self, message_id: str) -> list[Attachment]:
        """File attachments decoded in memory; item and reference attachments are ignored."""
        response = await self._request(
            "GET", f"{self._mailbox()}/messages/{message_id}/attachments"
        )
        result: list[Attachment] = []
        for node in response.json().get("value", []):
            if node.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            data = base64.b64decode(node.get("contentBytes") or "")
            result.append(
                Attachment(
                    name=node.get("name") or "attachment",
                    content_type=node.get("contentType") or "application/octet-stream",
                    size=int(node.get("size") or len(data)),
                    data=data,
                )
            )
        return result

    # --- writing ----------------------------------------------------------------

    async def send(self, message: OutboundMessage) -> None:
        """Send immediately (``sendMail``). No ids come back; prefer ``send_tracked``."""
        await self._request(
            "POST",
            f"{self._mailbox()}/sendMail",
            json={"message": message.to_graph(), "saveToSentItems": True},
        )

    async def create_draft(self, message: OutboundMessage) -> MessageIds:
        """Create a draft in the Drafts folder. Exchange assigns the ids at creation.

        This is the path for human approval: the reviewer can open the draft
        in Outlook, and ``send_draft`` sends exactly what was reviewed.
        """
        response = await self._request(
            "POST", f"{self._mailbox()}/messages", json=message.to_graph()
        )
        return MessageIds.from_graph(response.json())

    async def send_draft(self, draft_id: str) -> None:
        """Send an existing draft. Its id changes when it moves to Sent Items."""
        await self._request("POST", f"{self._mailbox()}/messages/{draft_id}/send")

    async def update_draft(
        self, draft_id: str, *, subject: str | None = None, html_body: str | None = None
    ) -> None:
        """Change a draft's subject and/or body before it is sent (an approver's edit)."""
        changes: dict[str, Any] = {}
        if subject is not None:
            changes["subject"] = subject
        if html_body is not None:
            changes["body"] = {"contentType": "HTML", "content": html_body}
        if changes:
            await self._request("PATCH", f"{self._mailbox()}/messages/{draft_id}", json=changes)

    async def reply_draft(
        self,
        message_id: str,
        html_body: str,
        *,
        headers: dict[str, str] | None = None,
        attachments: Sequence[Attachment] = (),
    ) -> MessageIds:
        """Create a reply draft in the same conversation (Graph sets In-Reply-To/References)."""
        node: dict[str, Any] = {"body": {"contentType": "html", "content": html_body}}
        if headers:
            node["internetMessageHeaders"] = [{"name": k, "value": v} for k, v in headers.items()]
        if attachments:
            node["attachments"] = [attachment_node(a) for a in attachments]
        response = await self._request(
            "POST", f"{self._mailbox()}/messages/{message_id}/createReply", json={"message": node}
        )
        return MessageIds.from_graph(response.json())

    async def find_sent(self, internet_message_id: str) -> MessageIds | None:
        """Locate a message in Sent Items by its RFC 5322 Message-ID."""
        escaped = internet_message_id.replace("'", "''")
        response = await self._request(
            "GET",
            f"{self._mailbox()}/mailFolders/sentitems/messages",
            params={
                "$filter": f"internetMessageId eq '{escaped}'",
                "$select": "id,internetMessageId,conversationId,webLink",
                "$top": "1",
            },
        )
        rows = response.json().get("value") or []
        return MessageIds.from_graph(rows[0]) if rows else None

    async def send_tracked(
        self, message: OutboundMessage, *, lookup_attempts: int = 5, lookup_delay: float = 2.0
    ) -> MessageIds:
        """Create a draft, send it, and return the ids of the sent copy.

        The draft's ``internetMessageId`` and ``conversationId`` are stable
        across the send; only the Graph ``id`` changes, so the sent copy is
        looked up by Message-ID (Sent Items can lag a few seconds).
        """
        draft = await self.create_draft(message)
        await self.send_draft(draft.id)
        if draft.internet_message_id:
            for attempt in range(lookup_attempts):
                sent = await self.find_sent(draft.internet_message_id)
                if sent:
                    return sent
                if attempt < lookup_attempts - 1:
                    await self._sleep(lookup_delay)
        logger.bind(message_id=draft.internet_message_id).warning(
            "sent message not found in Sent Items yet; returning draft ids"
        )
        return draft

    async def delete_message(self, message_id: str) -> None:
        """Permanently remove a message (used by tests and to discard a rejected draft)."""
        await self._request("DELETE", f"{self._mailbox()}/messages/{message_id}")


# --- helpers ----------------------------------------------------------------------


def _backoff_delay(attempt: int) -> float:
    return min(_BACKOFF_BASE * (2.0**attempt), _BACKOFF_CAP)


def _retry_after(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After")
    if not value:
        return _DEFAULT_RETRY_AFTER
    try:
        seconds: float = float(value)
    except ValueError:
        return _DEFAULT_RETRY_AFTER
    return max(seconds, 0.0)


def _error_details(response: httpx.Response) -> dict[str, Any]:
    details: dict[str, Any] = {"status": response.status_code}
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return details
    if error.get("code"):
        details["code"] = error["code"]
    if error.get("message"):
        details["message"] = str(error["message"])[:300]
    return details
