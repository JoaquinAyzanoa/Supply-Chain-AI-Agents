"""send_po: the confirmed order's PDF rides along with the cover email."""

from __future__ import annotations

from typing import Any

from sc_core.llm.testing import ScriptedChatClient, tool_call_result
from sc_core.mail.models import OutboundMessage
from sc_core.schema.a2a import SupplierCommsTask
from supplier_comms.models import DraftOutput
from supplier_comms.testing import FakePorts, demo_context
from tests.unit.graph.toy import FakeApprovalPorts

COVER = DraftOutput(
    subject="Orden de compra confirmada",
    html_body="<p>Adjuntamos la orden de compra. Confirmen la fecha.</p><p>Equipo de Compras</p>",
)


def _script(chat: ScriptedChatClient) -> None:
    chat.responses.extend([tool_call_result("get_po_lines", {"po_name": "P00015"}), "ok", COVER])


async def test_pdf_attached_listed_in_approval_and_sent(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _script(chat)
    agent = make_agent()
    paused = await agent.run(SupplierCommsTask(kind="send_po", case_id="po1", po_name="P00015"))
    assert paused.status == "awaiting_approval"
    assert paused.outbound is not None and paused.outbound.attachments == ["P00015.pdf"]
    draft = ports.drafts["draft1"]
    assert isinstance(draft, OutboundMessage) and len(draft.attachments) == 1
    attachment = draft.attachments[0]
    assert attachment.name == "P00015.pdf" and attachment.content_type == "application/pdf"
    assert attachment.data == ports.pdf_bytes and attachment.size == len(ports.pdf_bytes)
    assert draft.to_graph()["attachments"][0]["@odata.type"] == "#microsoft.graph.fileAttachment"
    payload = approval_ports.created[0]["payload"]
    assert (
        payload["attachments"] == ["P00015.pdf"]
        and "pdf" not in payload.get("html_body", "").lower()
    )
    snapshot = await agent.snapshot("po1")
    assert "%PDF" not in str(snapshot)  # the bytes never land in the checkpoint

    sent = await agent.resume("po1", {"approval_id": 101, "status": "approved"})
    assert sent.status == "sent" and ports.sent_ids == ["draft1"]
    assert sent.outbound is not None and sent.outbound.kind == "send_po"


async def test_send_po_refuses_an_unconfirmed_order(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    ports.contexts["P00016"] = demo_context(name="P00016").model_copy(update={"state": "draft"})
    result = await make_agent().run(
        SupplierCommsTask(kind="send_po", case_id="po2", po_name="P00016")
    )
    assert result.status == "failed" and "not a confirmed order" in result.outcome.summary
    assert chat.calls == [] and ports.drafts == {}


async def test_rfq_has_no_attachments(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    _script(chat)
    paused = await make_agent().run(
        SupplierCommsTask(kind="send_rfq", case_id="po3", po_name="P00015")
    )
    assert paused.outbound is not None and paused.outbound.attachments == []
    draft = ports.drafts["draft1"]
    assert isinstance(draft, OutboundMessage) and draft.attachments == []
