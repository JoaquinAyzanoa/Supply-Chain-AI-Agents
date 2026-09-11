"""FakeGraph behaves like the real client in the ways the services rely on."""

import pytest

from sc_core.mail.errors import GraphError
from sc_core.mail.models import Attachment, OutboundMessage
from sc_core.mail.protocol import MailClient
from sc_core.mail.testing import FakeGraph


def test_fake_satisfies_protocol() -> None:
    client: MailClient = FakeGraph()
    assert client is not None


async def test_delta_is_a_cursor() -> None:
    fake = FakeGraph()
    fake.receive(subject="[P00015] one", sender="a@x.com", body="uno")
    first = await fake.inbox_delta(None)
    assert [m.subject for m in first.messages] == ["[P00015] one"]

    fake.receive(subject="two", sender="b@x.com")
    second = await fake.inbox_delta(first.delta_link)
    assert [m.subject for m in second.messages] == ["two"]
    third = await fake.inbox_delta(second.delta_link)
    assert third.messages == [] and third.delta_link == second.delta_link


async def test_message_details_and_deletion() -> None:
    fake = FakeGraph()
    pdf = Attachment(name="q.pdf", content_type="application/pdf", size=3, data=b"pdf")
    msg = fake.receive(
        subject="s", sender="a@x.com", body="hola", headers={"X-SC-PO": "P00015"}, attachments=[pdf]
    )
    assert await fake.get_body_text(msg.id) == "hola"
    assert await fake.get_headers(msg.id) == {"x-sc-po": "P00015"}
    assert (await fake.attachments(msg.id))[0].is_pdf
    assert (await fake.get_message(msg.id)).has_attachments
    link = (await fake.inbox_delta(None)).delta_link
    await fake.delete_message(msg.id)
    page = await fake.inbox_delta(link)
    assert page.messages == [] and msg.id in fake.deleted
    with pytest.raises(GraphError):
        await fake.get_body_text(msg.id)


async def test_send_tracked_and_reply_threading() -> None:
    fake = FakeGraph()
    out = OutboundMessage(to=["v@p.com"], subject="RFQ", html_body="<p>x</p>").tagged("P00015")
    sent = await fake.send_tracked(out)
    assert sent.id.startswith("sent") and sent.internet_message_id and fake.drafts == {}
    assert fake.sent[0][1] == out
    assert await fake.find_sent(sent.internet_message_id) == sent

    inbound = fake.receive(
        subject="Re: RFQ", sender="v@p.com", conversation_id=sent.conversation_id
    )
    reply = await fake.reply_draft(inbound.id, "<p>gracias</p>", headers={"x-sc-po": "P00015"})
    assert reply.conversation_id == sent.conversation_id
    assert fake.drafts[reply.id]["reply_to"] == inbound.id  # type: ignore[index]


async def test_fail_next_injects_one_failure() -> None:
    fake = FakeGraph()
    fake.fail_next()
    with pytest.raises(GraphError):
        await fake.me()
    assert (await fake.me())["mail"] == "scai.compras@outlook.com"
