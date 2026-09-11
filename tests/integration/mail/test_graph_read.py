"""Read operations against the real bot mailbox."""

import pytest

from sc_core.mail.graph import GraphMailClient

pytestmark = [pytest.mark.integration, pytest.mark.graph]


async def test_me_is_the_bot_mailbox(graph_client: GraphMailClient) -> None:
    me = await graph_client.me()
    address = (me.get("mail") or me.get("userPrincipalName") or "").lower()
    assert address.endswith("@outlook.com"), me


async def test_delta_roundtrip(graph_client: GraphMailClient) -> None:
    first = await graph_client.inbox_delta(None, page_size=10)
    assert first.delta_link.startswith("https://graph.microsoft.com/")
    again = await graph_client.inbox_delta(first.delta_link)
    assert again.delta_link.startswith("https://graph.microsoft.com/")
    assert len(again.messages) <= len(first.messages) + 5  # nothing but very recent mail


async def test_message_details_when_inbox_has_mail(graph_client: GraphMailClient) -> None:
    page = await graph_client.inbox_delta(None, page_size=5)
    if not page.messages:
        pytest.skip("inbox is empty; send the bot an email to exercise body/headers")
    msg = page.messages[0]
    text = await graph_client.get_body_text(msg.id)
    assert isinstance(text, str)
    headers = await graph_client.get_headers(msg.id)
    assert all(k == k.lower() for k in headers)
    assert "message-id" in headers or "from" in headers
    attachments = await graph_client.attachments(msg.id)
    assert isinstance(attachments, list)
