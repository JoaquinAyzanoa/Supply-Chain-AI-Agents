"""A message no rule could link.

When the sender is a known partner or there are open orders to choose from,
the supplier agent reads the message and picks (``resolve_unlinked``). When
neither holds, no agent can do better than a person: the event is escalated
straight away, without a model call.
"""

from __future__ import annotations

from typing import Any

from director.router import Dispatch, Route
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.events import BaseEvent, InboundMailUnlinked


def handle(event: BaseEvent) -> Route:
    assert isinstance(event, InboundMailUnlinked)
    if event.partner_id is None and not event.open_po_names:
        return Route(
            case_kind="unlinked",
            conversation_id=event.conversation_id,
            escalate="message from an unknown sender with no open orders to match",
            details=email_facts(event),
        )
    task = SupplierCommsTask(
        kind="resolve_unlinked",
        case_id=event.case_id,
        graph_message_id=event.graph_message_id,
        candidate_po_names=list(event.open_po_names),
    )
    return Route(
        case_kind="unlinked",
        partner_id=event.partner_id,
        conversation_id=event.conversation_id,
        dispatches=[Dispatch(agent="supplier_comms", task=task)],
    )


def email_facts(event: InboundMailUnlinked) -> dict[str, Any]:
    """What a person needs to find the email: identifiers and the Outlook link, no content."""
    return {
        k: v
        for k, v in {
            "graph_message_id": event.graph_message_id,
            "conversation_id": event.conversation_id,
            "sender_address": event.sender_address,
            "has_attachments": event.has_attachments,
            "web_link": event.web_link,
        }.items()
        if v is not None
    }
