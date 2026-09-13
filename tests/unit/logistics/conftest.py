"""A logistics agent on fakes: scripted model, in-memory ports, fake carrier and approvals."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from logistics.agent import LogisticsAgent
from logistics.carriers import FakeCarrierTracking
from logistics.graph import Deps, build_graph
from logistics.testing import FakeLogisticsPorts, demo_context, demo_receipt
from sc_core.graph import ApprovalGateway, memory_checkpointer
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.testing import ScriptedChatClient
from supplier_comms.models import InboundMeta
from tests.unit.graph.toy import FakeApprovalPorts


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def ports() -> FakeLogisticsPorts:
    p = FakeLogisticsPorts()
    p.base.contexts["P00015"] = demo_context()
    p.base.inbound["notice1"] = (
        "Estimados, despachamos hoy por DHL, guía 1234567890, llega el 5 de octubre."
    )
    p.base.metas["notice1"] = InboundMeta(
        graph_message_id="notice1", sender_address="ventas.hidraulica.sc@gmail.com"
    )
    p.receipts[42] = demo_receipt(short_by=1)
    p.open_picking_ids[7] = [42]
    return p


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def approval_ports() -> FakeApprovalPorts:
    return FakeApprovalPorts()


@pytest.fixture
def tracker() -> FakeCarrierTracking:
    return FakeCarrierTracking()


@pytest.fixture
def make_agent(
    ports: FakeLogisticsPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
    tracker: FakeCarrierTracking,
) -> Any:
    def factory(**overrides: Any) -> LogisticsAgent:
        gateway = ApprovalGateway(
            approval_ports,
            agent_name="logistics",
            callback_url="http://logistics:8000/approvals/callback",
            callback_secret="s",
            approver_user_id=2,
            deadline_days=2,
        )
        kwargs: dict[str, Any] = {
            "ports": ports,
            "chat": chat,
            "approvals": gateway,
            "tracker": tracker,
            "langfuse": LangfuseCfg(enabled=False),
            "today": lambda: date(2026, 9, 30),
            "sleep": _no_sleep,
        }
        kwargs.update(overrides)
        return LogisticsAgent(
            build_graph(Deps(**kwargs), memory_checkpointer()), model="fake-model", ports=ports
        )

    return factory
