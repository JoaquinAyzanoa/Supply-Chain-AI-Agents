"""A2A handler: task JSON in, result JSON out; invalid tasks never reach the graph."""

from __future__ import annotations

import json
from typing import Any

from inventory_planning.agent import InventoryPlanningAgent
from inventory_planning.handler import SKILLS, InventoryPlanningHandler, agent_spec
from inventory_planning.nodes.explain import Explanation
from sc_core.llm.testing import ScriptedChatClient


class _Provider:
    def __init__(self, agent: InventoryPlanningAgent) -> None:
        self.agent = agent
        self.calls = 0

    async def get(self) -> InventoryPlanningAgent:
        self.calls += 1
        return self.agent


async def test_valid_task_runs_and_maps_statuses(make_agent: Any, chat: ScriptedChatClient) -> None:
    chat.responses.extend(
        [
            Explanation(product=ref, headline="h", reasoning="r", recommended_action="a")
            for ref in ("CBEA-LHN", "LODC-XDN", "990-011-007")
        ]
    )
    chat.responses.append("Resumen.")
    handler = InventoryPlanningHandler(_Provider(make_agent()))
    reply = await handler.handle(json.dumps({"kind": "daily_plan", "case_id": "case_h"}), {})
    assert reply.status == "input_required"
    result = json.loads(reply.text)
    assert result["outcome"]["status"] == "awaiting_approval"
    assert len(result["proposal"]["lines"]) == 4


async def test_invalid_task_fails_without_a_graph_call(
    make_agent: Any, chat: ScriptedChatClient
) -> None:
    provider = _Provider(make_agent())
    handler = InventoryPlanningHandler(provider)
    reply = await handler.handle('{"kind": "what_if", "case_id": "x"}', {})  # product_ids missing
    assert reply.status == "failed" and json.loads(reply.text)["code"] == "invalid_task"
    assert provider.calls == 0 and chat.calls == []


def test_agent_card_lists_the_three_skills() -> None:
    spec = agent_spec("http://inventory_planning:8000/")
    assert spec.url == "http://inventory_planning:8000/a2a"
    assert [s.id for s in SKILLS] == ["daily_plan", "review_product", "what_if"]
