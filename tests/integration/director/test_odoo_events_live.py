"""Odoo → director round trip on the running stack.

Confirms a fresh RFQ for the demo supplier through Odoo RPC; the addon's
automation rule posts a signed ``odoo.purchase_confirmed`` to the director
container, which opens a case and sends ``send_po`` to supplier_comms (a
real model run and a real draft in the bot mailbox, left awaiting approval).

Needs: ``just up`` with rebuilt images, ``just migrate``, ``just odoo-upgrade``,
``just odoo-configure``, the seeded dataset, and ``SC_E2E_ODOO_EVENTS=1``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import uuid4

import pytest

from sc_core.infra.db import Database
from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import NewOrderLine
from sc_core.odoo.repositories import PartnerRepo, PurchaseOrderRepo
from sc_core.schema.events import event_id_for

pytestmark = [pytest.mark.integration, pytest.mark.odoo, pytest.mark.graph, pytest.mark.llm]

SUPPLIER_EMAIL = "ventas.hidraulica.sc@gmail.com"


async def _wait_for(fetch: Any, *, seconds: float = 60.0) -> Any:
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        row = await fetch()
        if row:
            return row
        if asyncio.get_running_loop().time() > deadline:
            return None
        await asyncio.sleep(2)


async def test_confirming_an_order_opens_a_case_and_sends_the_order(
    odoo: OdooClient, odoo_admin: OdooClient, live_db: Database
) -> None:
    if os.environ.get("SC_E2E_ODOO_EVENTS") != "1":
        pytest.skip("set SC_E2E_ODOO_EVENTS=1 to run the Odoo round trip (sends a real email)")
    params = await odoo_admin.call_model(
        "ir.config_parameter", "get_param", "sc_agents.events_secret"
    )
    if not params:
        pytest.skip("run `just odoo-configure` first")
    partner = await PartnerRepo(odoo).find_by_email(SUPPLIER_EMAIL)
    if partner is None:
        pytest.skip("demo supplier missing; run `just odoo-seed`")
    company = await PartnerRepo(odoo).commercial_partner(partner)
    product_ids = await odoo.search("product.product", [["default_code", "=", "CBEA-LHN"]], limit=1)
    if not product_ids:
        pytest.skip("demo catalogue missing; run `just odoo-seed`")

    orders = PurchaseOrderRepo(odoo)
    rfq = await orders.create_rfq(
        company.id,
        [NewOrderLine(product_id=int(product_ids[0]), product_qty=2)],
        external_ref=f"e2e-odoo-events-{uuid4().hex[:8]}",
        origin="phase 6 round trip",
    )
    confirmed = await PurchaseOrderRepo(odoo_admin).confirm(rfq.id)
    assert confirmed.state == "purchase"

    event_id = event_id_for("odoo.purchase_confirmed", rfq.id, "purchase")
    inbox = await _wait_for(
        lambda: live_db.fetch_one(
            "SELECT event_type, handled_at, result FROM event_inbox WHERE event_id = %s",
            (event_id,),
        )
    )
    assert inbox is not None, "the director never received odoo.purchase_confirmed"
    assert inbox["event_type"] == "odoo.purchase_confirmed"

    handled = await _wait_for(
        lambda: live_db.fetch_one(
            "SELECT result FROM event_inbox WHERE event_id = %s AND handled_at IS NOT NULL",
            (event_id,),
        ),
        seconds=240,
    )
    assert handled is not None, "the workflow did not finish in time"
    case = await live_db.fetch_one(
        "SELECT case_id, kind, status, summary FROM cases WHERE po_name = %s", (rfq.name,)
    )
    assert case is not None and case["kind"] == "eta"
    assert case["status"] in ("awaiting_approval", "done"), case
    events = await live_db.fetch_all(
        "SELECT kind, payload FROM case_events WHERE case_id = %s ORDER BY id", (case["case_id"],)
    )
    kinds = [e["kind"] for e in events]
    assert kinds[:3] == ["event_received", "promise", "task_sent"]
    assert any(e["kind"] == "result" for e in events)
    print(
        f"\n>>> {rfq.name} confirmed -> case {case['case_id']} {case['status']}: {case['summary']}"
    )
