"""GraphMailClient write side against a scripted transport."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from sc_core.mail.graph import GraphMailClient
from sc_core.mail.models import OutboundMessage

from .conftest import ScriptedGraph, Sleeps, ok

Factory = Callable[..., GraphMailClient]

OUT = OutboundMessage(
    to=["ventas@proveedor.com"], subject="Solicitud de cotización", html_body="<p>Hola</p>"
).tagged("P00015", "case_1")

DRAFT = {
    "id": "draft1",
    "internetMessageId": "<m1@outlook.com>",
    "conversationId": "conv1",
    "webLink": "https://outlook.live.com/draft1",
}
SENT = {**DRAFT, "id": "sent1", "webLink": "https://outlook.live.com/sent1"}


def test_tagged_sets_subject_and_headers() -> None:
    assert OUT.subject == "[P00015] Solicitud de cotización"
    assert OUT.headers == {"x-sc-po": "P00015", "x-sc-case": "case_1"}
    assert OUT.tagged("P00015").subject == OUT.subject  # idempotent


async def test_send_posts_sendmail(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [httpx.Response(202)]
    await client_factory().send(OUT)
    assert graph.requests[0].method == "POST"
    assert graph.requests[0].url.path.endswith("/me/sendMail")
    body = graph.json_of(0)
    assert body["saveToSentItems"] is True
    assert body["message"]["subject"] == "[P00015] Solicitud de cotización"
    assert {"name": "x-sc-po", "value": "P00015"} in body["message"]["internetMessageHeaders"]


async def test_create_and_send_draft(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [ok(DRAFT, 201), httpx.Response(202)]
    client = client_factory()
    draft = await client.create_draft(OUT)
    assert draft.id == "draft1" and draft.internet_message_id == "<m1@outlook.com>"
    assert graph.requests[0].url.path.endswith("/me/messages")
    assert graph.json_of(0)["toRecipients"][0]["emailAddress"]["address"] == "ventas@proveedor.com"
    await client.send_draft(draft.id)
    assert graph.requests[1].url.path.endswith("/me/messages/draft1/send")


async def test_reply_draft_uses_create_reply(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [ok({**DRAFT, "id": "reply1"}, 201)]
    ids = await client_factory().reply_draft(
        "AAMk1", "<p>Gracias</p>", headers={"x-sc-po": "P00015"}
    )
    assert ids.id == "reply1" and ids.conversation_id == "conv1"
    assert graph.requests[0].url.path.endswith("/me/messages/AAMk1/createReply")
    node = graph.json_of(0)["message"]
    assert node["body"] == {"contentType": "html", "content": "<p>Gracias</p>"}
    assert node["internetMessageHeaders"] == [{"name": "x-sc-po", "value": "P00015"}]


async def test_find_sent_filters_by_message_id(
    graph: ScriptedGraph, client_factory: Factory
) -> None:
    graph.script += [ok({"value": [SENT]})]
    found = await client_factory().find_sent("<m'1@outlook.com>")
    assert found is not None and found.id == "sent1"
    params = graph.requests[0].url.params
    assert params["$filter"] == "internetMessageId eq '<m''1@outlook.com>'"
    assert graph.requests[0].url.path.endswith("/me/mailFolders/sentitems/messages")


async def test_send_tracked_waits_for_sent_items(
    graph: ScriptedGraph, client_factory: Factory, sleeps: Sleeps
) -> None:
    graph.script += [ok(DRAFT, 201), httpx.Response(202), ok({"value": []}), ok({"value": [SENT]})]
    sent = await client_factory().send_tracked(OUT, lookup_delay=1.5)
    assert sent.id == "sent1" and sent.web_link.endswith("sent1")
    assert sleeps.delays == [1.5]


async def test_send_tracked_falls_back_to_draft_ids(
    graph: ScriptedGraph, client_factory: Factory
) -> None:
    graph.script += [ok(DRAFT, 201), httpx.Response(202), ok({"value": []}), ok({"value": []})]
    sent = await client_factory().send_tracked(OUT, lookup_attempts=2, lookup_delay=0)
    assert sent.id == "draft1" and sent.internet_message_id == "<m1@outlook.com>"


async def test_update_draft_patches_subject_and_body(
    graph: ScriptedGraph, client_factory: Factory
) -> None:
    graph.script += [httpx.Response(200, json=DRAFT)]
    await client_factory().update_draft(
        "draft1", subject="[P00015] Better", html_body="<p>Edited</p>"
    )
    assert graph.requests[0].method == "PATCH"
    assert graph.requests[0].url.path.endswith("/me/messages/draft1")
    assert graph.json_of(0) == {
        "subject": "[P00015] Better",
        "body": {"contentType": "HTML", "content": "<p>Edited</p>"},
    }
    await client_factory().update_draft("draft1")  # nothing to change: no request
    assert len(graph.requests) == 1


async def test_delete_message(graph: ScriptedGraph, client_factory: Factory) -> None:
    graph.script += [httpx.Response(204)]
    await client_factory().delete_message("AAMk1")
    assert graph.requests[0].method == "DELETE"
    assert graph.requests[0].url.path.endswith("/me/messages/AAMk1")
