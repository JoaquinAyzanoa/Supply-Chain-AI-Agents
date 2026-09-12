"""planning_runs and planning_lines against a real Postgres (migration 005)."""

from __future__ import annotations

from datetime import date

import pytest

from inventory_planning.runs import PostgresRunStore
from sc_core.infra.db import Database
from sc_core.schema.planning import ReplenishmentLine, ReplenishmentProposal

pytestmark = pytest.mark.integration


def _line(run_id: str, product_id: int, **overrides: object) -> ReplenishmentLine:
    base: dict[str, object] = {
        "line_id": f"{run_id}:{product_id}",
        "product_id": product_id,
        "product_ref": f"P-{product_id}",
        "warehouse_id": 1,
        "on_hand": 20,
        "incoming": 10,
        "position": 30,
        "forecast_daily": 1.0,
        "forecast_method": "ses",
        "sigma_daily": 0.8,
        "history_periods": 104,
        "lead_time_days": 30,
        "sigma_lead_time_days": 7.5,
        "service_level": 0.95,
        "review_period_days": 7,
        "abc_class": "B",
        "ss": 14.26,
        "rop": 44.26,
        "order_up_to": 51.26,
        "proposed_min": 45,
        "proposed_max": 52,
        "order_qty": 21.26,
        "supplier_id": 20,
        "unit_price": 104.0,
        "currency": "PEN",
        "action": "update_rule_and_rfq",
        "exception": "stockout_risk",
    }
    return ReplenishmentLine(**{**base, **overrides})  # type: ignore[arg-type]


async def test_proposal_lines_and_apply_roundtrip(db: Database) -> None:
    store = PostgresRunStore(db)
    proposal = ReplenishmentProposal(
        run_id="run_a",
        as_of=date(2026, 9, 14),
        warehouse_id=1,
        warehouse_code="WH",
        lines=[_line("run_a", 1), _line("run_a", 2, action="none", exception=None)],
        summary="dos productos",
        totals={"lines": 2.0, "rfq_lines": 1.0},
    )
    await store.save_proposal(proposal, case_id="plan_1", kind="daily_plan", status="proposed")
    await store.save_proposal(proposal, case_id="plan_1", kind="daily_plan", status="proposed")
    assert await store.latest_run_id(kind="daily_plan", as_of=date(2026, 9, 14)) == "run_a"
    assert await store.latest_run_id(kind="daily_plan", as_of=date(2026, 9, 15)) is None

    await store.set_status("run_a", "awaiting_approval", approval_id=7)
    await store.mark_applied(
        "run_a", {"run_a:1": {"orderpoint_id": 3, "po_name": "P00070"}}, accepted={"run_a:1"}
    )
    await store.set_status("run_a", "applied")
    run = await db.fetch_one(
        "SELECT status, approval_id, totals FROM planning_runs WHERE run_id = 'run_a'"
    )
    assert run == {
        "status": "applied",
        "approval_id": 7,
        "totals": {"lines": 2.0, "rfq_lines": 1.0},
    }
    rows = await db.fetch_all(
        "SELECT line_id, accepted, applied, inputs, outputs "
        "FROM planning_lines WHERE run_id = 'run_a' ORDER BY product_id"
    )
    assert rows[0]["accepted"] is True and rows[0]["applied"]["po_name"] == "P00070"
    assert rows[1]["accepted"] is False and rows[1]["applied"] is None
    assert rows[0]["inputs"]["lead_time_days"] == 30 and rows[0]["outputs"]["rop"] == 44.26
