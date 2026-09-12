"""The loop, end to end: send with token -> arrives -> sync links it -> director gets the event.

The bot mails itself (like the phase 2 write test) so the test needs no
human. ``test_supplier_reply`` covers the real supplier mailbox and is
opt-in because someone has to reply from Gmail.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import uuid4

import pytest

from sc_core.mail.graph import GraphMailClient
from sc_core.mail.models import OutboundMessage
from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import PurchaseOrder

pytestmark = [pytest.mark.integration, pytest.mark.graph, pytest.mark.odoo]

SUPPLIER = "ventas.hidraulica.sc@gmail.com"


async def _any_po(world: dict[str, Any]) -> PurchaseOrder:
    orders = await world["pos"].find([["state", "in", ["draft", "sent", "purchase"]]], limit=1)
    if not orders:
        pytest.skip("Odoo has no purchase order to link to (install demo data)")
    return orders[0]


async def _sync_until_linked(world: dict[str, Any], po_name: str, *, timeout: float) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        report = await world["runner"].run()
        if po_name in report.linked_po_names:
            return report
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail(f"no message linked to {po_name} within {timeout:.0f}s")
        await asyncio.sleep(3)


async def _cleanup(
    graph: GraphMailClient, odoo_admin: OdooClient, *, message_ids: list[str], link_ids: list[int]
) -> None:
    for message_id in message_ids:
        try:
            await graph.delete_message(message_id)
        except Exception:  # noqa: BLE001 - best effort
            pass
    if link_ids:
        await odoo_admin.unlink("sc.mail.link", link_ids)


async def test_self_sent_message_is_linked_and_event_delivered(
    world: dict[str, Any],
    graph: GraphMailClient,
    odoo_admin: OdooClient,
    director_inbox: Any,
) -> None:
    po = await _any_po(world)
    me = await graph.me()
    address = me.get("mail") or me["userPrincipalName"]
    marker = uuid4().hex[:8]
    sent = await graph.send_tracked(
        OutboundMessage(
            to=[address], subject=f"e2e {marker}", html_body=f"<p>e2e {marker}</p>"
        ).tagged(po.name, f"case_{marker}")
    )
    to_delete = [sent.id]
    link_ids: list[int] = []
    try:
        report = await _sync_until_linked(world, po.name, timeout=90)
        assert report.status == "ok" and report.errors == 0

        # the Odoo link exists, carries the web link and the case id
        rows = await world["links"].find([["graph_conversation_id", "=", sent.conversation_id]])
        assert rows, "no sc.mail.link created for the conversation"
        link = rows[0]
        link_ids = [r.id for r in rows]
        to_delete.append(link.graph_message_id)
        assert link.po_id.id == po.id and link.direction == "in"
        assert link.web_link and link.web_link.startswith("https://")
        assert link.confidence == "exact"

        # the director accepted the signed event
        linked = director_inbox.of_type("inbound_mail.linked")
        assert [e.po_name for e in linked] == [po.name]
        assert linked[0].rule == "header" and linked[0].case_id == link.case_id

        # a second sync sees nothing new
        again = await world["runner"].run()
        assert again.linked == 0 and again.errors == 0

        # rewinding the delta link re-fetches the message: it is skipped, not re-linked
        await world["state"].save_delta_link("me", world["baseline_delta"], status="rewind")
        replay = await world["runner"].run()
        assert replay.linked == 0 and replay.skipped >= 1 and replay.errors == 0
        assert len(director_inbox.of_type("inbound_mail.linked")) == 1
    finally:
        await _cleanup(graph, odoo_admin, message_ids=to_delete, link_ids=link_ids)


@pytest.mark.skipif(
    os.environ.get("SC_E2E_SUPPLIER") != "1",
    reason="needs a human reply from the supplier mailbox; set SC_E2E_SUPPLIER=1",
)
async def test_supplier_reply(
    world: dict[str, Any],
    graph: GraphMailClient,
    odoo_admin: OdooClient,
    director_inbox: Any,
) -> None:
    """Send an RFQ to the demo supplier; a person replies from Gmail; the reply links by rule."""
    po = await _any_po(world)
    marker = uuid4().hex[:8]
    sent = await graph.send_tracked(
        OutboundMessage(
            to=[SUPPLIER],
            subject=f"Solicitud de cotización {marker}",
            html_body="<p>Estimados, por favor confirmen precio y fecha de entrega. "
            "Responda a este correo sin cambiar el asunto.</p>",
        ).tagged(po.name, f"case_{marker}")
    )
    wait = float(os.environ.get("SC_E2E_WAIT_SECONDS", "600"))
    print(
        f"\n>>> Reply from {SUPPLIER} to the message '[{po.name}] Solicitud de cotización "
        f"{marker}' within {wait:.0f}s (keep the subject).",
        flush=True,
    )
    link_ids: list[int] = []
    to_delete: list[str] = []
    try:
        report = await _sync_until_linked(world, po.name, timeout=wait)
        assert report.errors == 0
        rows = await world["links"].find([["graph_conversation_id", "=", sent.conversation_id]])
        assert rows and rows[0].po_id.id == po.id
        link_ids = [r.id for r in rows]
        to_delete = [r.graph_message_id for r in rows]
        linked = director_inbox.of_type("inbound_mail.linked")
        assert linked and linked[0].po_name == po.name
        assert linked[0].rule in ("header", "subject_token", "conversation", "in_reply_to")
    finally:
        await _cleanup(graph, odoo_admin, message_ids=to_delete, link_ids=link_ids)
