"""GraphMailClient read side against a scripted transport."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from sc_core.mail.errors import DeltaExpired, GraphError, MailAuthRequired
from sc_core.mail.graph import GraphMailClient
from sc_core.mail.models import EmailAddress, InboundMessage, OutboundMessage

from .conftest import FakeTokens, ScriptedGraph, Sleeps, ok

Factory = Callable[..., GraphMailClient]

MSG = {
    "id": "AAMk1",
    "conversationId": "conv1",
    "internetMessageId": "<abc@example.com>",
    "subject": "Re: [P00015] RFQ",
    "from": {"emailAddress": {"name": "Ventas", "address": "Ventas@Proveedor.COM"}},
    "toRecipients": [{"emailAddress": {"address": "scai.compras@outlook.com"}}],
    "receivedDateTime": "2026-09-12T10:00:00Z",
    "hasAttachments": True,
    "isRead": False,
    "webLink": "https://outlook.live.com/mail/0/deeplink?ItemID=AAMk1",
}


# --- models ---------------------------------------------------------------------


def test_inbound_message_from_graph() -> None:
    msg = InboundMessage.from_graph(MSG)
    assert msg.sender == EmailAddress(address="Ventas@Proveedor.COM", name="Ventas")
    assert msg.sender.normalized == "ventas@proveedor.com" and msg.sender.domain == "proveedor.com"
    assert msg.received_at == datetime(2026, 9, 12, 10, tzinfo=UTC)
    assert msg.to[0].address == "scai.compras@outlook.com"
    assert msg.has_attachments and not msg.is_read


def test_inbound_message_tolerates_missing_fields() -> None:
    msg = InboundMessage.from_graph({"id": "x"})
    assert msg.sender is None and msg.to == [] and msg.received_at is None and msg.subject is None


def test_outbound_to_graph_and_header_rule() -> None:
    out = OutboundMessage(
        to=["a@b.com"],
        cc=["c@d.com"],
        subject="[P00015] RFQ",
        html_body="<p>hi</p>",
        headers={"x-sc-po": "P00015"},
    )
    node = out.to_graph()
    assert node["toRecipients"] == [{"emailAddress": {"address": "a@b.com"}}]
    assert node["ccRecipients"][0]["emailAddress"]["address"] == "c@d.com"
    assert node["internetMessageHeaders"] == [{"name": "x-sc-po", "value": "P00015"}]
    with pytest.raises(ValueError, match="x-"):
        OutboundMessage(to=["a@b.com"], subject="s", html_body="", headers={"po": "1"}).to_graph()


# --- delta ----------------------------------------------------------------------


async def test_delta_follows_pages_and_returns_delta_link(
    graph: ScriptedGraph, client_factory: Factory
) -> None:
    graph.script += [
        ok(
            {"value": [MSG], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next?skiptoken=1"}
        ),
        ok(
            {
                "value": [
                    {**MSG, "id": "AAMk2"},
                    {"id": "gone", "@removed": {"reason": "deleted"}},
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=Z",
            }
        ),
    ]
    page = await client_factory().inbox_delta(None, page_size=2)
    assert [m.id for m in page.messages] == ["AAMk1", "AAMk2"]
    assert page.removed_ids == ["gone"]
    assert page.delta_link.endswith("token=Z")
    first = graph.requests[0]
    assert first.url.path.endswith("/me/mailFolders/inbox/messages/delta")
    assert first.url.params["$top"] == "2" and "conversationId" in first.url.params["$select"]
    assert first.headers["Authorization"] == "Bearer tok"
    assert graph.urls()[1] == "https://graph.microsoft.com/v1.0/next?skiptoken=1"


async def test_delta_resumes_from_stored_link(
    graph: ScriptedGraph, client_factory: Factory
) -> None:
    graph.script += [ok({"value": [], "@odata.deltaLink": "https://g/delta?token=Y"})]
    page = await client_factory().inbox_delta("https://g/delta?token=X")
    assert page.messages == [] and page.delta_link == "https://g/delta?token=Y"
    assert graph.urls() == ["https://g/delta?token=X"]


async def test_delta_410_is_delta_expired(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [httpx.Response(410, json={"error": {"code": "SyncStateNotFound"}})]
    with pytest.raises(DeltaExpired):
        await client_factory().inbox_delta("https://g/delta?token=old")


async def test_delta_without_links_is_an_error(
    graph: ScriptedGraph, client_factory: Factory
) -> None:
    graph.script += [ok({"value": []})]
    with pytest.raises(GraphError, match="deltaLink"):
        await client_factory().inbox_delta(None)


# --- retries -------------------------------------------------------------------


async def test_429_honours_retry_after(
    graph: ScriptedGraph, client_factory: Factory, sleeps: Sleeps
) -> None:
    graph.script += [
        httpx.Response(429, headers={"Retry-After": "7"}),
        ok({"value": [], "@odata.deltaLink": "d"}),
    ]
    await client_factory().inbox_delta(None)
    assert sleeps.delays == [7.0]


async def test_429_without_header_uses_default(
    graph: ScriptedGraph, client_factory: Factory, sleeps: Sleeps
) -> None:
    graph.script += [httpx.Response(429), ok({"displayName": "Bot"})]
    await client_factory().me()
    assert sleeps.delays == [5.0]


async def test_5xx_and_transport_errors_back_off(
    graph: ScriptedGraph, client_factory: Factory, sleeps: Sleeps
) -> None:
    graph.script += [httpx.Response(503), httpx.ConnectError("boom"), ok({"displayName": "Bot"})]
    assert (await client_factory().me())["displayName"] == "Bot"
    assert sleeps.delays == [1.0, 2.0]


async def test_gives_up_after_max_retries(
    graph: ScriptedGraph, client_factory: Factory, sleeps: Sleeps
) -> None:
    graph.script += [httpx.Response(502)] * 3
    with pytest.raises(GraphError) as exc:
        await client_factory(max_retries=2).me()
    assert exc.value.retryable is True and exc.value.details["status"] == 502
    assert len(sleeps.delays) == 2


async def test_401_refreshes_token_once(
    graph: ScriptedGraph, client_factory: Factory, tokens: FakeTokens
) -> None:
    graph.script += [httpx.Response(401), ok({"displayName": "Bot"})]
    await client_factory().me()
    assert tokens.calls == [False, True]
    assert graph.requests[1].headers["Authorization"] == "Bearer tok2"


async def test_401_twice_requires_login(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [httpx.Response(401), httpx.Response(401)]
    with pytest.raises(MailAuthRequired):
        await client_factory().me()


async def test_other_4xx_is_not_retried(
    graph: ScriptedGraph, client_factory: Factory, sleeps: Sleeps
) -> None:
    graph.script += [
        httpx.Response(404, json={"error": {"code": "ErrorItemNotFound", "message": "gone"}})
    ]
    with pytest.raises(GraphError) as exc:
        await client_factory().get_body_text("nope")
    assert exc.value.retryable is False
    assert exc.value.details == {
        "service": "graph",
        "status": 404,
        "code": "ErrorItemNotFound",
        "message": "gone",
    }
    assert sleeps.delays == []


# --- message details ---------------------------------------------------------------


async def test_body_text_prefers_unique_body(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [
        ok(
            {
                "body": {"contentType": "text", "content": "full thread"},
                "uniqueBody": {"contentType": "text", "content": "just this reply"},
            }
        )
    ]
    assert await client_factory().get_body_text("AAMk1") == "just this reply"
    req = graph.requests[0]
    assert req.headers["Prefer"] == 'outlook.body-content-type="text"'
    assert req.url.params["$select"] == "body,uniqueBody"


async def test_body_text_falls_back_to_body(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [ok({"body": {"contentType": "text", "content": "only body"}})]
    assert await client_factory().get_body_text("AAMk1") == "only body"


async def test_headers_are_lowercased(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [
        ok(
            {
                "internetMessageHeaders": [
                    {"name": "X-SC-PO", "value": "P00015"},
                    {"name": "In-Reply-To", "value": "<abc@example.com>"},
                ]
            }
        )
    ]
    headers = await client_factory().get_headers("AAMk1")
    assert headers == {"x-sc-po": "P00015", "in-reply-to": "<abc@example.com>"}


async def test_attachments_decode_files_and_skip_items(
    graph: ScriptedGraph, client_factory: Factory
) -> None:
    pdf = b"%PDF-1.4 fake"
    graph.script += [
        ok(
            {
                "value": [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": "cotizacion.pdf",
                        "contentType": "application/pdf",
                        "size": len(pdf),
                        "contentBytes": base64.b64encode(pdf).decode(),
                    },
                    {"@odata.type": "#microsoft.graph.itemAttachment", "name": "forwarded"},
                ]
            }
        )
    ]
    result = await client_factory().attachments("AAMk1")
    assert len(result) == 1
    assert result[0].name == "cotizacion.pdf" and result[0].is_pdf and result[0].data == pdf
    assert graph.requests[0].url.path.endswith("/me/messages/AAMk1/attachments")


async def test_get_message(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [ok(MSG)]
    msg = await client_factory().get_message("AAMk1")
    assert msg.conversation_id == "conv1"
    assert graph.requests[0].url.params["$select"].startswith("id,conversationId")
