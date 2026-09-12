"""A supplier message linked to an order by rule → ``supplier_comms.handle_inbound``."""

from __future__ import annotations

from director.router import Dispatch, Route
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.events import BaseEvent, InboundMailLinked


def handle(event: BaseEvent) -> Route:
    assert isinstance(event, InboundMailLinked)
    task = SupplierCommsTask(
        kind="handle_inbound",
        case_id=event.case_id,
        po_name=event.po_name,
        graph_message_id=event.graph_message_id,
    )
    return Route(
        case_kind="inbound",
        po_name=event.po_name,
        conversation_id=event.conversation_id,
        dispatches=[Dispatch(agent="supplier_comms", task=task)],
    )
