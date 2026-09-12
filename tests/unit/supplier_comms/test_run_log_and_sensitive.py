"""Every terminal path leaves the checkpoint without email text and closes the run log."""

from __future__ import annotations

from datetime import date
from typing import Any

from sc_core.graph import SENSITIVE_KEYS, cleared
from sc_core.llm.testing import ScriptedChatClient, tool_call_result
from sc_core.schema.a2a import Classification, QuotationData, SupplierCommsTask
from supplier_comms.models import DraftOutput, InboundMeta
from supplier_comms.testing import FakePorts

MSG = "AAMk-x"
DRAFT = DraftOutput(subject="Seguimiento", html_body="<p>Seguimiento de la orden.</p>")


async def test_outbound_paths_clear_state_and_log_runs(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    agent = make_agent()
    # approved -> sent
    chat.responses.extend([tool_call_result("get_po_lines", {"po_name": "P00015"}), "ok", DRAFT])
    paused = await agent.run(SupplierCommsTask(kind="follow_up", case_id="s1", po_name="P00015"))
    assert ports.runs[paused.run_id]["status"] == "awaiting_approval"
    assert ports.runs[paused.run_id]["po_id"] == 7 and ports.runs[paused.run_id]["case_id"] == "s1"
    mid = await agent.snapshot("s1")
    assert mid["outbound_html"]  # still needed while paused (the approval shows it)
    sent = await agent.resume("s1", {"approval_id": 101, "status": "approved"})
    assert cleared(await agent.snapshot("s1")) and ports.runs[sent.run_id]["status"] == "sent"
    # rejected
    chat.responses.extend([tool_call_result("get_po_lines", {"po_name": "P00015"}), "ok", DRAFT])
    await agent.run(SupplierCommsTask(kind="follow_up", case_id="s2", po_name="P00015"))
    rejected = await agent.resume("s2", {"approval_id": 102, "status": "rejected"})
    assert (
        cleared(await agent.snapshot("s2")) and ports.runs[rejected.run_id]["status"] == "rejected"
    )


async def test_inbound_paths_clear_state(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    agent = make_agent()
    ports.inbound[MSG] = "confirmamos entrega el 20 de octubre"
    ports.metas[MSG] = InboundMeta(graph_message_id=MSG, sender_address="v@x.com")
    task = SupplierCommsTask(
        kind="handle_inbound", case_id="i1", po_name="P00015", graph_message_id=MSG
    )
    chat.responses.extend(
        [
            Classification(kind="eta_update", confidence=0.9, reason="fecha"),
            QuotationData(
                eta_date_raw="20 de octubre", eta_date=date(2026, 10, 20), confidence=0.9
            ),
        ]
    )
    paused = await agent.run(task)
    assert paused.status == "awaiting_approval"
    assert (await agent.snapshot("i1"))["inbound_text"]  # kept while the human decides
    applied = await agent.resume("i1", {"approval_id": 101, "status": "approved"})
    snapshot = await agent.snapshot("i1")
    assert cleared(snapshot) and all(snapshot.get(k) in (None, []) for k in SENSITIVE_KEYS)
    assert ports.runs[applied.run_id]["status"] == "applied"

    chat.responses.append(Classification(kind="other", confidence=0.9, reason="acuse"))
    no_action = await agent.run(task.model_copy(update={"case_id": "i2"}))
    assert no_action.status == "no_action" and cleared(await agent.snapshot("i2"))
    assert ports.runs[no_action.run_id]["status"] == "no_action"
