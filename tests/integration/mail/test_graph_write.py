"""Write operations against the real bot mailbox: the bot emails itself.

Sends one real message per run and deletes what it created afterwards.
"""

import asyncio
from uuid import uuid4

import pytest

from sc_core.mail import po_token
from sc_core.mail.graph import GraphMailClient
from sc_core.mail.models import OutboundMessage

pytestmark = [pytest.mark.integration, pytest.mark.graph]


async def test_draft_send_receive_reply_roundtrip(graph_client: GraphMailClient) -> None:
    me = await graph_client.me()
    address = me.get("mail") or me["userPrincipalName"]
    marker = uuid4().hex[:8]
    message = OutboundMessage(
        to=[address],
        subject=f"integration {marker}",
        html_body=f"<p>self-test {marker}</p>",
    ).tagged("P00015", f"case_{marker}")

    # 1. a draft gets its ids at creation; discard it (this only proves drafting)
    draft = await graph_client.create_draft(message)
    assert draft.internet_message_id and draft.conversation_id
    await graph_client.delete_message(draft.id)
    baseline = await graph_client.inbox_delta(None, page_size=50)

    # 2. draft + send + lookup of the sent copy by Message-ID
    sent = await graph_client.send_tracked(message)
    assert sent.internet_message_id and sent.conversation_id

    # 3. it arrives in the same inbox with token and headers intact
    received = None
    for _ in range(15):
        page = await graph_client.inbox_delta(baseline.delta_link)
        for msg in page.messages:
            if msg.subject and marker in msg.subject:
                received = msg
        baseline = page
        if received:
            break
        await asyncio.sleep(2)
    assert received is not None, "self-sent message did not arrive within 30s"
    assert po_token.parse(received.subject) == "P00015"
    headers = await graph_client.get_headers(received.id)
    assert headers.get("x-sc-po") == "P00015" and headers.get("x-sc-case") == f"case_{marker}"
    assert marker in await graph_client.get_body_text(received.id)
    assert received.conversation_id == sent.conversation_id

    # 4. a reply draft lands in the same conversation
    reply = await graph_client.reply_draft(
        received.id, f"<p>reply {marker}</p>", headers=po_token.headers_for("P00015")
    )
    assert reply.conversation_id == received.conversation_id

    # cleanup: the reply draft, the received copy and the sent copy
    for message_id in (reply.id, received.id, sent.id):
        await graph_client.delete_message(message_id)
