"""A supplier_comms agent on fakes: scripted model, in-memory ports and approvals."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from sc_core.graph import ApprovalGateway, memory_checkpointer
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.testing import ScriptedChatClient
from supplier_comms.agent import SupplierCommsAgent
from supplier_comms.graph import Deps, build_graph
from supplier_comms.testing import FakePorts, demo_context
from tests.unit.graph.toy import FakeApprovalPorts


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def ports() -> FakePorts:
    p = FakePorts()
    p.contexts["P00015"] = demo_context()
    return p


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def approval_ports() -> FakeApprovalPorts:
    return FakeApprovalPorts()


@pytest.fixture
def make_agent(
    ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> Any:
    def factory(**overrides: Any) -> SupplierCommsAgent:
        gateway = ApprovalGateway(
            approval_ports,
            agent_name="supplier_comms",
            callback_url="http://supplier_comms:8000/approvals/callback",
            callback_secret="s",
            approver_user_id=2,
            deadline_days=2,
        )
        deps = Deps(
            ports=ports,
            chat=chat,
            approvals=gateway,
            langfuse=LangfuseCfg(enabled=False),
            today=lambda: date(2026, 9, 12),
            sleep=_no_sleep,
            **overrides,
        )
        return SupplierCommsAgent(build_graph(deps, memory_checkpointer()), model="fake-model")

    return factory
