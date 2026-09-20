"""An invoice matching agent on fakes: scripted model, in-memory ports and approvals."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from invoice_match.graph.builder import Deps, build_graph
from invoice_match.graph.runner import InvoiceMatchAgent
from invoice_match.testing import FakeInvoicePorts, demo_context
from sc_core.graph import ApprovalGateway, memory_checkpointer
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.testing import ScriptedChatClient
from supplier_comms.domain.models import InboundMeta, LineView


def received_context() -> Any:
    """The demo order with both lines received and nothing billed yet."""
    ctx = demo_context()
    return ctx.model_copy(
        update={
            "lines": [
                LineView(**{**line.model_dump(), "qty_received": line.qty, "qty_invoiced": 0.0})
                for line in ctx.lines
            ]
        }
    )


@pytest.fixture
def ports() -> FakeInvoicePorts:
    p = FakeInvoicePorts()
    p.base.contexts["P00015"] = received_context()
    p.base.inbound["inv1"] = "Adjuntamos la factura F001-000123 por la orden P00015."
    p.base.attachments["inv1"] = [
        "[factura.pdf]\nFACTURA ELECTRONICA F001-000123\nFecha: 03/10/2026\n"
        'Bomba hidraulica 2HP  2  500.00  1000.00\nManguera 1/2"  20  12.50  250.00\n'
        "Subtotal 1250.00 IGV 225.00 Total 1475.00 PEN"
    ]
    p.base.metas["inv1"] = InboundMeta(
        graph_message_id="inv1",
        sender_address="ventas.hidraulica.sc@gmail.com",
        has_attachments=True,
    )
    p.base.partners["ventas.hidraulica.sc@gmail.com"] = 42
    return p


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def approval_ports() -> Any:
    from tests.unit.graph.toy import FakeApprovalPorts

    return FakeApprovalPorts()


@pytest.fixture
def make_agent(ports: FakeInvoicePorts, chat: ScriptedChatClient, approval_ports: Any) -> Any:
    def factory(**overrides: Any) -> InvoiceMatchAgent:
        gateway = ApprovalGateway(
            approval_ports,
            agent_name="invoice_match",
            callback_url="http://invoice_match:8000/approvals/callback",
            callback_secret="s",
            approver_user_id=2,
            deadline_days=2,
            policy=overrides.pop("policy", None),
            auto_actions=overrides.pop("auto_actions", None),
        )
        kwargs: dict[str, Any] = {
            "ports": ports,
            "chat": chat,
            "approvals": gateway,
            "langfuse": LangfuseCfg(enabled=False),
            "today": lambda: date(2026, 10, 4),
        }
        kwargs.update(overrides)
        return InvoiceMatchAgent(
            build_graph(Deps(**kwargs), memory_checkpointer()), model="fake-model", ports=ports
        )

    return factory
