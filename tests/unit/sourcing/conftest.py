"""A sourcing agent on fakes: scripted model, in-memory ports and approvals."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sc_core.graph import ApprovalGateway, memory_checkpointer
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.testing import ScriptedChatClient
from sourcing.agent import SourcingAgent
from sourcing.graph import Deps, build_graph
from sourcing.models import BasketLine, OrderRef
from sourcing.nodes.common import Limits
from sourcing.testing import HIDRAULICA, VALVE, FakeSourcingPorts, demo_entries, demo_options
from tests.unit.graph.toy import FakeApprovalPorts

NOW = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


@pytest.fixture
def ports() -> FakeSourcingPorts:
    p = FakeSourcingPorts()
    p.products[VALVE] = "[CBEA-LHN] Válvula de contrabalance"
    p.last_paid_by_product[VALVE] = 104.16
    p.options = demo_options()
    p.entries = demo_entries()
    # P00081: the planner's RFQ to Proveedor Hidraulica, sent, one valve line quoted at 104.16
    p.orders["P00081"] = OrderRef(
        po_id=81,
        po_name="P00081",
        partner_id=HIDRAULICA,
        partner_name="Proveedor Hidraulica",
        state="sent",
        currency="USD",
        currency_id=1,
    )
    p.baskets["P00081"] = [
        BasketLine(
            product_id=VALVE,
            product="[CBEA-LHN] Válvula de contrabalance",
            qty=10,
            last_paid=104.16,
            currency="USD",
        )
    ]
    # P00077: a confirmed order to Hidraulica, late
    p.orders["P00077"] = OrderRef(
        po_id=77,
        po_name="P00077",
        partner_id=HIDRAULICA,
        partner_name="Proveedor Hidraulica",
        state="purchase",
        currency="USD",
        currency_id=1,
    )
    p.baskets["P00077"] = list(p.baskets["P00081"])
    return p


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def approval_ports() -> FakeApprovalPorts:
    return FakeApprovalPorts()


@pytest.fixture
def make_agent(
    ports: FakeSourcingPorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> Any:
    def factory(**overrides: Any) -> SourcingAgent:
        gateway = ApprovalGateway(
            approval_ports,
            agent_name="sourcing",
            callback_url="http://sourcing:8000/approvals/callback",
            callback_secret="s",
            approver_user_id=2,
            deadline_days=2,
            policy=overrides.pop("policy", None),
        )
        limits = overrides.pop("limits", Limits())

        async def read_limits() -> Limits:
            return limits

        kwargs: dict[str, Any] = {
            "ports": ports,
            "chat": chat,
            "approvals": gateway,
            "limits": read_limits,
            "langfuse": LangfuseCfg(enabled=False),
            "now": lambda: NOW,
        }
        kwargs.update(overrides)
        return SourcingAgent(
            build_graph(Deps(**kwargs), memory_checkpointer()), model="fake-model", ports=ports
        )

    return factory
