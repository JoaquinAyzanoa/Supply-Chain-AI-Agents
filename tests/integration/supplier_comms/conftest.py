"""The supplier agent on real Odoo, Graph, DeepSeek and Langfuse, with a throwaway Postgres.

The live fixtures (mailbox session, Odoo clients, migrated database) come
from ``tests/integration/conftest.py``. The approval callback URL points
nowhere on purpose: the test resumes the graph itself after resolving the
approval through Odoo RPC, so the running container is never involved.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from sc_core.graph import ApprovalGateway, OdooApprovalPorts, build_checkpointer
from sc_core.infra import tracing
from sc_core.infra.db import Database
from sc_core.infra.settings import Settings
from sc_core.llm import get_chat_client
from sc_core.llm.registry import Registry
from sc_core.mail.graph import GraphMailClient
from sc_core.mail.outbound import PostgresOutboundMailStore
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import (
    ActivityRepo,
    AgentRunRepo,
    ApprovalRepo,
    MailLinkRepo,
    PartnerRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
)
from supplier_comms import AGENT_NAME
from supplier_comms.agent import SupplierCommsAgent
from supplier_comms.graph import Deps, build_graph
from supplier_comms.ports import LivePorts

SUPPLIER_EMAIL = "ventas.hidraulica.sc@gmail.com"


@pytest.fixture(scope="session")
def llm_ready(live_settings: Settings) -> Settings:
    provider = Registry.load().provider_for(live_settings.llm.model_for(AGENT_NAME))
    if not provider.configured:
        pytest.skip(f"{provider.api_key_env} not set; the agent needs a real model")
    tracing.configure_tracing(live_settings)
    return live_settings


@pytest.fixture
async def live_agent(
    llm_ready: Settings, odoo: OdooClient, graph: GraphMailClient, db: Database
) -> AsyncIterator[dict[str, Any]]:
    ports = LivePorts(
        odoo=odoo,
        purchase_orders=PurchaseOrderRepo(odoo),
        partners=PartnerRepo(odoo),
        mail_links=MailLinkRepo(odoo),
        supplier_info=SupplierInfoRepo(odoo),
        agent_runs=AgentRunRepo(odoo),
        graph=graph,
        outbound=PostgresOutboundMailStore(db),
    )
    gateway = ApprovalGateway(
        OdooApprovalPorts(ApprovalRepo(odoo), ActivityRepo(odoo)),
        agent_name=AGENT_NAME,
        callback_url="http://127.0.0.1:9/approvals/callback",  # unreachable on purpose
        callback_secret=llm_ready.events.signing_secret.get_secret_value() or None,
        approver_user_id=llm_ready.agents.approver_user_id,
        deadline_days=1,
    )
    chat = get_chat_client(AGENT_NAME, settings=llm_ready)
    deps = Deps(ports=ports, chat=chat, approvals=gateway, langfuse=llm_ready.langfuse)
    agent = SupplierCommsAgent(
        build_graph(deps, await build_checkpointer(db)),
        model=llm_ready.llm.model_for(AGENT_NAME),
        ports=ports,
    )
    yield {"agent": agent, "ports": ports, "approvals": ApprovalRepo(odoo)}
    tracing.flush()
