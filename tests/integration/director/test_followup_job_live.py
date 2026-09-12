"""The follow-up job on the running stack: a backdated order gets a ``request_eta``.

Backdates ``date_planned`` of an open seeded order for the demo supplier,
fires ``po_followups`` through the scheduler's run-now endpoint (scheduler →
director → supplier_comms, all in containers), waits for the tick's summary
on the director's inbox row and expects a pending ``send_email`` approval
on that order in Odoo.

Needs the rebuilt stack (`just up`, `just migrate`), the seeded dataset and
``SC_E2E_FOLLOWUPS=1`` (a real model run; the email waits for approval).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from sc_core.infra.db import Database
from sc_core.odoo.client import OdooClient
from sc_core.odoo.repositories import ApprovalRepo, PartnerRepo, PurchaseOrderRepo

pytestmark = [pytest.mark.integration, pytest.mark.odoo, pytest.mark.graph, pytest.mark.llm]

SUPPLIER_EMAIL = "ventas.hidraulica.sc@gmail.com"


async def test_backdated_order_gets_an_eta_request(
    odoo: OdooClient, odoo_admin: OdooClient, live_db: Database
) -> None:
    if os.environ.get("SC_E2E_FOLLOWUPS") != "1":
        pytest.skip("set SC_E2E_FOLLOWUPS=1 to run the follow-up job end to end")
    partner = await PartnerRepo(odoo).find_by_email(SUPPLIER_EMAIL)
    if partner is None:
        pytest.skip("demo supplier missing; run `just odoo-seed`")
    company = await PartnerRepo(odoo).commercial_partner(partner)
    orders = PurchaseOrderRepo(odoo)
    candidates = await orders.find(
        [
            ["partner_id", "=", company.id],
            ["state", "=", "purchase"],
            ["receipt_status", "!=", "full"],
            ["sc_needs_human", "=", False],
        ],
        limit=1,
        order="id asc",
    )
    if not candidates:
        pytest.skip("no open confirmed order for the demo supplier; run `just odoo-seed`")
    po = candidates[0]

    # two days late: the po_late rule asks the supplier for a new date
    late = datetime.now(UTC) - timedelta(days=2)
    for line in await orders.lines(po.id):
        await orders.set_line_date_planned(line.id, late, source="estimated", run_id="e2e")

    scheduler = os.environ.get("SC_SCHEDULER_URL", "http://localhost:8012").rstrip("/")
    try:
        response = httpx.post(f"{scheduler}/jobs/po_followups/run-now", timeout=900)
    except httpx.HTTPError as exc:
        pytest.skip(f"scheduler not reachable at {scheduler}: {exc}")
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "ok", run

    # the director answered 202 and ran the job in the background: wait for its summary
    summary = None
    for _ in range(150):
        row = await live_db.fetch_one(
            "SELECT result FROM event_inbox WHERE event_type = 'scheduler.tick' "
            "AND payload->>'run_id' = %s AND handled_at IS NOT NULL",
            (run["run_id"],),
        )
        if row and row["result"] and "replay" in row["result"]:
            summary = row["result"]
            break
        await asyncio.sleep(4)
    assert summary is not None, "the follow-up job did not finish in time"
    [update] = summary["updates"]
    assert update["kind"] == "job" and update["detail"]["tasks_sent"] >= 1, update

    pending = await ApprovalRepo(odoo_admin).pending_for_po(po.id)
    assert pending, f"no approval on {po.name} after the follow-up job"
    assert pending[0].kind == "send_email"
    print(f"\n>>> {po.name} backdated -> approval {pending[0].id}: {pending[0].summary}")
