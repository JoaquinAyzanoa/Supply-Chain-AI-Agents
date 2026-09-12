"""A planner on fakes: demo catalogue, scripted model, in-memory stores and approvals."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from inventory_planning.agent import InventoryPlanningAgent
from inventory_planning.graph import Deps, build_graph
from inventory_planning.policy import MemoryParamsStore
from inventory_planning.runs import MemoryRunStore
from inventory_planning.testing import FakeDataPorts, FakePublisher, FakeWritePorts, demo_ports
from sc_core.graph import ApprovalGateway, memory_checkpointer
from sc_core.infra.settings import LangfuseCfg, PlanningCfg
from sc_core.llm.testing import ScriptedChatClient
from tests.unit.graph.toy import FakeApprovalPorts

AS_OF = date(2026, 9, 14)


@pytest.fixture
def data() -> FakeDataPorts:
    return demo_ports(as_of=AS_OF)


@pytest.fixture
def writes() -> FakeWritePorts:
    return FakeWritePorts()


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def approval_ports() -> FakeApprovalPorts:
    return FakeApprovalPorts()


@pytest.fixture
def runs() -> MemoryRunStore:
    return MemoryRunStore()


@pytest.fixture
def publisher() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def make_agent(
    data: FakeDataPorts,
    writes: FakeWritePorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
    runs: MemoryRunStore,
    publisher: FakePublisher,
) -> Any:
    def factory(**overrides: Any) -> InventoryPlanningAgent:
        gateway = ApprovalGateway(
            approval_ports,
            agent_name="inventory_planning",
            callback_url="http://inventory_planning:8000/approvals/callback",
            callback_secret="s",
            approver_user_id=2,
            deadline_days=2,
        )
        kwargs: dict[str, Any] = {
            "data": data,
            "writes": writes,
            "params": MemoryParamsStore(),
            "runs": runs,
            "chat": chat,
            "approvals": gateway,
            "cfg": PlanningCfg(product_category="", history_days=730),
            "publish": publisher.publish,
            "langfuse": LangfuseCfg(enabled=False),
            "today": lambda: AS_OF,
        }
        kwargs.update(overrides)
        deps = Deps(**kwargs)
        return InventoryPlanningAgent(
            build_graph(deps, memory_checkpointer()), model="fake-model", writes=deps.writes
        )

    return factory
