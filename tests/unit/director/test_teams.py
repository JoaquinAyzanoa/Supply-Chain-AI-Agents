"""Approvals mirrored to a Teams channel: a card with a link back, never a body."""

from __future__ import annotations

import json
from typing import Any

import httpx

from director.teams import TeamsNotifier, approval_card


def test_the_card_carries_the_summary_the_facts_and_a_link_to_the_inbox() -> None:
    message = approval_card(
        approval_id=44,
        kind="internal_request",
        summary="Internal request from ana@empresa.com: 2 item(s)",
        control_tower_url="https://tower.example/",
        po_name=None,
        case_code="C00050",
        agent="supplier_comms",
    )
    [attachment] = message["attachments"]
    card = attachment["content"]
    assert card["body"][0]["text"] == "Approval #44 needs a decision"
    assert card["body"][1]["text"] == "Internal request from ana@empresa.com: 2 item(s)"
    facts = {f["title"]: f["value"] for f in card["body"][2]["facts"]}
    assert facts == {"Kind": "Internal request", "Case": "C00050", "Asked by": "supplier_comms"}
    assert card["actions"][0]["url"] == "https://tower.example/approvals?id=44"


async def test_the_notifier_posts_the_card_and_survives_a_refusal() -> None:
    posted: list[dict[str, Any]] = []
    status = {"code": 200}

    def handle(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(status["code"])

    notifier = TeamsNotifier(
        "https://hooks.example/abc",
        control_tower_url="https://tower.example",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    await notifier.approval_requested(
        approval_id=7,
        kind="send_email",
        summary="Reminder to Proveedor Hidraulica",
        po_name="P00080",
    )
    assert posted[0]["attachments"][0]["content"]["body"][2]["facts"][1] == {
        "title": "Order",
        "value": "P00080",
    }
    status["code"] = 400
    await notifier.approval_requested(
        approval_id=8, kind="award", summary="x"
    )  # logged, not raised
    assert len(posted) == 2
    await notifier.aclose()
