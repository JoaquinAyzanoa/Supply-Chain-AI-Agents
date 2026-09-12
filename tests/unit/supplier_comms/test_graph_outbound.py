"""Outbound flow: draft with a tool call -> approval -> send / reject / auto-send / failures."""

from __future__ import annotations

from typing import Any

import pytest

from sc_core.llm.testing import ScriptedChatClient, tool_call_result
from sc_core.schema.a2a import SupplierCommsTask
from supplier_comms.models import DraftOutput
from supplier_comms.routers.approvals import ApprovalCallback
from supplier_comms.testing import SUPPLIER_EMAIL, FakePorts, demo_context
from tests.unit.graph.toy import FakeApprovalPorts

DRAFT = DraftOutput(
    subject="Solicitud de cotización",
    html_body="<p>Estimados, solicitamos cotización de la orden.</p><p>Equipo de Compras</p>",
)


def _script_draft(chat: ScriptedChatClient) -> None:
    chat.responses.extend(
        [
            tool_call_result("get_po_lines", {"po_name": "P00015"}),
            "Tengo las líneas; redacto el correo.",
            DRAFT,
        ]
    )


async def test_send_rfq_pauses_on_approval_then_sends(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _script_draft(chat)
    agent = make_agent()
    task = SupplierCommsTask(kind="send_rfq", case_id="case_rfq", po_name="P00015")

    paused = await agent.run(task)
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101
    assert paused.outbound is not None and paused.outbound.draft_id == "draft1"
    assert paused.outbound.subject == "[P00015] Solicitud de cotización"
    assert paused.outbound.to == [SUPPLIER_EMAIL]

    # the model was asked with tools, the tool ran, then the structured final call
    assert (
        chat.calls[0].options["tools"] and chat.calls[2].options["response_format"] == "DraftOutput"
    )
    assert "Bomba" in chat.last_prompt_text()  # tool result reached the model
    # draft exists in Outlook with token and headers; the approval carries the full body
    draft = ports.drafts["draft1"]
    assert (
        draft.subject == "[P00015] Solicitud de cotización" and draft.headers["x-sc-po"] == "P00015"
    )
    assert draft.headers["x-sc-case"] == "case_rfq"
    payload = approval_ports.created[0]["payload"]
    assert payload["html_body"] == DRAFT.html_body and payload["draft_id"] == "draft1"
    assert (
        approval_ports.created[0]["kind"] == "send_email"
        and approval_ports.created[0]["po_id"] == 7
    )
    assert ports.sent_ids == []

    sent = await agent.resume(
        "case_rfq", {"approval_id": 101, "status": "approved", "resolved_by": "ana"}
    )
    assert sent.status == "sent" and sent.run_id == paused.run_id
    assert ports.sent_ids == ["draft1"]
    assert ports.outbound_records[0]["po_name"] == "P00015"
    assert ports.outbound_records[0]["graph_message_id"] == "sent1"  # the sent copy, not the draft
    assert ports.links[0] == {
        "po_id": 7,
        "graph_message_id": "sent1",
        "direction": "out",
        "case_id": "case_rfq",
    }
    assert "Open in Outlook" in ports.notes[-1][1]
    assert sent.outbound is not None and sent.outbound.sent_message_id == "sent1"
    assert len(approval_ports.created) == 1  # no second approval on resume


async def test_edited_draft_is_patched_before_sending(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    _script_draft(chat)
    agent = make_agent()
    paused = await agent.run(
        SupplierCommsTask(kind="send_rfq", case_id="case_edit", po_name="P00015")
    )
    usage = ports.runs[paused.run_id]["usage"]  # the budget's totals reach sc.agent.run
    assert usage["calls"] >= 1 and usage["input_tokens"] > 0 and "usd" in usage
    # exactly what Odoo posts to the callback after a Control Tower decision
    callback = ApprovalCallback.model_validate(
        {
            "approval_id": 101,
            "status": "approved",
            "thread_id": "case_edit",
            "resolved_by": "sc_agent_bot",
            "resolved_by_name": "Ana",
            "details": {"subject": "RFQ for pumps", "html_body": "<p>Edited by Ana</p>"},
        }
    )
    sent = await agent.resume("case_edit", callback.decision())
    assert sent.status == "sent" and ports.sent_ids == ["draft1"]
    [patch] = ports.updated_drafts
    # the PO token survives the edit so replies still resolve to the order
    assert patch == {
        "draft_id": "draft1",
        "subject": "[P00015] RFQ for pumps",
        "html_body": "<p>Edited by Ana</p>",
    }
    assert sent.outbound is not None and sent.outbound.subject == "[P00015] RFQ for pumps"
    assert ports.runs[sent.run_id]["usage"]["calls"] == 0  # the resume made no model call


async def test_reject_sends_nothing(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    _script_draft(chat)
    agent = make_agent()
    await agent.run(
        SupplierCommsTask(kind="follow_up", case_id="c2", po_name="P00015", days_silent=4)
    )
    result = await agent.resume(
        "c2", {"approval_id": 101, "status": "rejected", "reason": "esperar"}
    )
    assert result.status == "rejected" and "esperar" in result.outcome.summary
    assert ports.sent_ids == [] and ports.links == []
    assert "rejected" in ports.notes[-1][1]
    assert "Days without a supplier reply: 4" in chat.calls[0].messages[1]["contents"][0]["text"]


async def test_auto_send_partner_skips_approval(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _script_draft(chat)
    agent = make_agent(auto_send_partner_ids=frozenset({42}))
    result = await agent.run(SupplierCommsTask(kind="request_eta", case_id="c3", po_name="P00015"))
    assert result.status == "sent" and approval_ports.created == []
    assert ports.sent_ids == ["draft1"]


async def test_resume_after_finish_is_idempotent(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    _script_draft(chat)
    agent = make_agent(auto_send_partner_ids=frozenset({42}))
    first = await agent.run(SupplierCommsTask(kind="send_rfq", case_id="c4", po_name="P00015"))
    again = await agent.resume("c4", {"approval_id": 0, "status": "approved"})
    assert again.status == first.status == "sent" and ports.sent_ids == ["draft1"]


async def test_unknown_po_and_supplier_without_email_fail_cleanly(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    agent = make_agent()
    missing = await agent.run(SupplierCommsTask(kind="send_rfq", case_id="c5", po_name="P09999"))
    assert missing.status == "failed" and "not found" in missing.outcome.summary
    ports.contexts["P00016"] = demo_context(emails=[], name="P00016")
    no_email = await agent.run(SupplierCommsTask(kind="send_rfq", case_id="c6", po_name="P00016"))
    assert no_email.status == "failed" and "no email" in no_email.outcome.summary
    assert chat.calls == []  # no model call without a recipient


async def test_sent_copy_lookup_retries_then_falls_back(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    _script_draft(chat)
    ports.find_sent_misses = 99
    agent = make_agent(auto_send_partner_ids=frozenset({42}))
    result = await agent.run(SupplierCommsTask(kind="send_rfq", case_id="c7", po_name="P00015"))
    assert result.status == "sent"
    assert ports.outbound_records[0]["graph_message_id"] == "draft1"  # fell back to the draft ids


async def test_email_language_follows_the_supplier_then_the_instance(
    make_agent: Any, chat: ScriptedChatClient, ports: FakePorts
) -> None:
    """The demo supplier reads Spanish (es_PE); one without a language gets the instance's."""
    chat.responses.extend([tool_call_result("get_po_lines", {"po_name": "P00015"}), "ok", DRAFT])
    await make_agent().run(SupplierCommsTask(kind="send_rfq", case_id="lang_es", po_name="P00015"))
    system_text = chat.calls[0].messages[0]["contents"][0]["text"]
    assert "Write the email in Spanish" in system_text and "Equipo de Compras" in system_text

    ports.contexts["P00016"] = demo_context(name="P00016").model_copy(update={"partner_lang": None})
    chat.responses.extend([tool_call_result("get_po_lines", {"po_name": "P00016"}), "ok", DRAFT])
    await make_agent(language="en").run(
        SupplierCommsTask(kind="send_rfq", case_id="lang_en", po_name="P00016")
    )
    system_text = chat.calls[-2].messages[0]["contents"][0]["text"]
    assert "Write the email in English" in system_text and "Purchasing Team" in system_text


async def test_a_crashing_run_is_logged_as_failed(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient
) -> None:
    chat.responses.append(RuntimeError("provider down"))
    agent = make_agent()
    with pytest.raises(RuntimeError):
        await agent.run(SupplierCommsTask(kind="send_rfq", case_id="case_crash", po_name="P00015"))
    [run] = [r for r in ports.runs.values() if r.get("case_id") == "case_crash"]
    assert run["status"] == "failed" and "provider down" in run["summary"]
    assert "calls" in run["usage"]


async def test_auto_send_by_kind_skips_approval_for_that_kind_only(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    from sc_core.infra.runtime_settings import MemoryRuntimeSettingsReader
    from sc_core.schema.runtime_settings import RuntimeSettings

    runtime = MemoryRuntimeSettingsReader(RuntimeSettings(auto_send_kinds=["request_eta"]))
    agent = make_agent(runtime=runtime)
    _script_draft(chat)
    eta = await agent.run(SupplierCommsTask(kind="request_eta", case_id="c5", po_name="P00015"))
    assert eta.status == "sent" and approval_ports.created == [] and ports.sent_ids == ["draft1"]

    _script_draft(chat)
    rfq = await agent.run(SupplierCommsTask(kind="send_rfq", case_id="c6", po_name="P00015"))
    assert rfq.status == "awaiting_approval" and len(approval_ports.created) == 1
