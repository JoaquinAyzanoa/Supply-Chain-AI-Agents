"""A2A handler: task JSON in, result JSON out; invalid tasks never reach the graph."""

from __future__ import annotations

import json
from typing import Any

from sc_core.llm.testing import ScriptedChatClient, tool_call_result
from supplier_comms.agent import SupplierCommsAgent
from supplier_comms.handler import SKILLS, SupplierCommsHandler, agent_spec
from supplier_comms.models import DraftOutput
from supplier_comms.testing import FakePorts


class _Provider:
    def __init__(self, agent: SupplierCommsAgent) -> None:
        self.agent = agent
        self.calls = 0

    async def get(self) -> SupplierCommsAgent:
        self.calls += 1
        return self.agent


async def test_valid_task_runs_and_maps_statuses(
    make_agent: Any, chat: ScriptedChatClient, ports: FakePorts
) -> None:
    chat.responses.extend(
        [
            tool_call_result("get_po_lines", {"po_name": "P00015"}),
            "ok",
            DraftOutput(subject="Solicitud", html_body="<p>Solicitud de cotización.</p>"),
        ]
    )
    handler = SupplierCommsHandler(_Provider(make_agent()))
    reply = await handler.handle(
        json.dumps({"kind": "send_rfq", "case_id": "case_h", "po_name": "P00015"}),
        {"case_id": "case_h"},
    )
    assert reply.status == "input_required"
    result = json.loads(reply.text)
    assert result["outcome"]["status"] == "awaiting_approval" and result["po_name"] == "P00015"
    assert ports.drafts  # the graph ran

    missing = await handler.handle(
        json.dumps({"kind": "send_rfq", "case_id": "case_h2", "po_name": "P09999"}), {}
    )
    assert missing.status == "failed" and json.loads(missing.text)["outcome"]["status"] == "failed"


async def test_invalid_task_fails_without_a_graph_call(
    make_agent: Any, chat: ScriptedChatClient
) -> None:
    provider = _Provider(make_agent())
    handler = SupplierCommsHandler(provider)
    reply = await handler.handle('{"kind": "send_rfq", "case_id": "x"}', {})  # po_name missing
    assert reply.status == "failed" and json.loads(reply.text)["code"] == "invalid_task"
    assert provider.calls == 0 and chat.calls == []


def test_agent_card_lists_the_five_skills() -> None:
    spec = agent_spec("http://supplier_comms:8000/")
    assert spec.url == "http://supplier_comms:8000/a2a" and spec.name == "supplier_comms"
    assert [s.id for s in spec.skills] == [
        "send_rfq",
        "request_eta",
        "follow_up",
        "handle_inbound",
        "resolve_unlinked",
    ]
    assert len(SKILLS) == 5
