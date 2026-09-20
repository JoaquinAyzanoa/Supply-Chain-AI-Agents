"""The daily plan end to end on fakes: proposal, approval, idempotent apply."""

from __future__ import annotations

from typing import Any

import pytest

from inventory_planning.graph.nodes.explain import Explanation
from inventory_planning.infra.runs import MemoryRunStore
from inventory_planning.testing import FakePublisher, FakeWritePorts
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import InventoryPlanningTask
from tests.unit.graph.toy import FakeApprovalPorts


def _explanation(ref: str) -> Explanation:
    return Explanation(
        product=ref,
        headline=f"Atención en {ref}.",
        reasoning="Los números de la regla lo justifican.",
        recommended_action="Aprobar la propuesta.",
    )


def _script(chat: ScriptedChatClient, exception_refs: list[str]) -> None:
    chat.responses.extend(_explanation(ref) for ref in exception_refs)
    chat.responses.append("Resumen de la corrida diaria: se revisaron cuatro productos.")


async def test_daily_plan_proposes_one_line_per_product_and_pauses(
    make_agent: Any,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
    runs: MemoryRunStore,
    writes: FakeWritePorts,
) -> None:
    agent = make_agent()
    # the run flags the three seed flaws: stockout risk, overstock, no supplier
    _script(chat, ["CBEA-LHN", "LODC-XDN", "990-011-007"])
    result = await agent.run(InventoryPlanningTask(kind="daily_plan", case_id="plan_1"))

    assert result.status == "awaiting_approval" and result.outcome.approval_id == 101
    assert result.proposal is not None
    proposal = result.proposal
    assert [ln.product_ref for ln in proposal.lines] == [
        "CBEA-LHN",
        "CXDA-XCN",
        "LODC-XDN",
        "990-011-007",
    ]
    by_ref = {ln.product_ref: ln for ln in proposal.lines}
    assert by_ref["CBEA-LHN"].exception == "stockout_risk"
    assert (
        by_ref["LODC-XDN"].exception == "no_supplier"
        and by_ref["LODC-XDN"].action == "manual_review"
    )
    assert by_ref["990-011-007"].exception == "overstock" and by_ref["990-011-007"].order_qty == 0
    assert by_ref["CXDA-XCN"].exception is None and by_ref["CXDA-XCN"].explanation is None
    # explanations only on exception lines; one model call each plus the summary
    assert all(ln.explanation for ln in proposal.lines if ln.exception)
    assert len(chat.calls) == 4 and proposal.summary.startswith("Resumen")
    # the approval carries the totals and the exceptions, hung on the warehouse
    [created] = approval_ports.created
    assert created["kind"] == "planning_run" and created["res_model"] == "stock.warehouse"
    assert created["res_id"] == 1 and created["payload"]["totals"] == proposal.totals
    [review] = approval_ports.reviews  # the To-Do hangs on the approval, not the warehouse
    assert review["res_model"] == "sc.approval" and review["res_id"] == 101
    assert [e["product_ref"] for e in created["payload"]["exceptions"]] == [
        "CBEA-LHN",
        "LODC-XDN",
        "990-011-007",
    ]
    assert proposal.totals["lines"] == 4 and proposal.totals["manual_review"] == 1
    assert proposal.totals["rfq_lines"] >= 1
    # the run and its lines are stored with numbers only
    assert runs.runs["run_" + result.run_id[4:]]["status"] == "proposed"
    stored = runs.lines[by_ref["CBEA-LHN"].line_id]
    assert stored["outputs"]["rop"] == by_ref["CBEA-LHN"].rop
    assert stored["inputs"]["lead_time_days"] == 30
    assert writes.orderpoints == [] and writes.rfqs == {}
    assert writes.runs[result.run_id]["status"] == "awaiting_approval"


async def test_approval_applies_accepted_lines_once(
    make_agent: Any,
    chat: ScriptedChatClient,
    writes: FakeWritePorts,
    runs: MemoryRunStore,
    publisher: FakePublisher,
) -> None:
    agent = make_agent()
    _script(chat, ["CBEA-LHN", "LODC-XDN", "990-011-007"])
    paused = await agent.run(InventoryPlanningTask(kind="daily_plan", case_id="plan_2"))
    assert paused.proposal is not None
    lines = {ln.product_ref: ln for ln in paused.proposal.lines}
    accepted = [lines["CBEA-LHN"].line_id, lines["990-011-007"].line_id]

    applied = await agent.resume(
        "plan_2",
        {
            "approval_id": 101,
            "status": "approved",
            "details": {
                "accepted_line_ids": accepted,
                "edits": {lines["CBEA-LHN"].line_id: {"order_qty": 30}},
            },
        },
    )
    assert applied.status == "applied" and applied.applied is not None
    assert applied.applied.orderpoints_written == 2  # CBEA (rule moved) and the kit (overstock)
    # the planner names no supplier: the buys go to the sourcing agent as needs
    assert applied.applied.rfqs_created == [] and applied.applied.lines_applied == 2
    assert applied.applied.needs_sent == 1
    rules = {r["product_id"]: r for r in writes.orderpoints}
    assert rules[1]["id"] == 1 and rules[1]["created"] is False  # existing rule updated
    assert rules[4]["max"] == lines["990-011-007"].proposed_max
    assert writes.rfqs == {}
    needs = [e for e in publisher.events if e.type == "sourcing.needs"]
    assert len(needs) == 1 and needs[0].run_id == applied.run_id
    [need] = needs[0].needs
    assert need.product == "CBEA-LHN" and need.qty == 30.0  # the edit
    assert need.expected_price == 104.0 and need.need_date is not None
    assert runs.runs[paused.run_id]["status"] == "applied"
    assert runs.lines[lines["CXDA-XCN"].line_id]["accepted"] is False
    assert runs.lines[lines["CBEA-LHN"].line_id]["applied"]["need"] is True

    # a second resume with the same decision changes nothing
    again = await agent.resume("plan_2", {"approval_id": 101, "status": "approved"})
    assert again.status == "applied" and len(writes.orderpoints) == 2
    assert writes.rfqs == {} and len(publisher.events) == 1  # the needs went out once


async def test_plain_approval_accepts_every_actionable_line(
    make_agent: Any, chat: ScriptedChatClient, writes: FakeWritePorts
) -> None:
    agent = make_agent()
    _script(chat, ["CBEA-LHN", "LODC-XDN", "990-011-007"])
    paused = await agent.run(InventoryPlanningTask(kind="daily_plan", case_id="plan_3"))
    assert paused.proposal is not None
    actionable = [
        ln
        for ln in paused.proposal.lines
        if ln.action in ("update_rule", "create_rfq", "update_rule_and_rfq")
    ]
    applied = await agent.resume("plan_3", {"approval_id": 101, "status": "approved"})
    assert applied.applied is not None and applied.applied.lines_applied == len(actionable)
    manual = next(ln for ln in paused.proposal.lines if ln.action == "manual_review")
    assert all(r["product_id"] != manual.product_id for r in writes.orderpoints)


async def test_rejection_writes_nothing(
    make_agent: Any, chat: ScriptedChatClient, writes: FakeWritePorts, runs: MemoryRunStore
) -> None:
    agent = make_agent()
    _script(chat, ["CBEA-LHN", "LODC-XDN", "990-011-007"])
    paused = await agent.run(InventoryPlanningTask(kind="daily_plan", case_id="plan_4"))
    rejected = await agent.resume(
        "plan_4", {"approval_id": 101, "status": "rejected", "reason": "esperar al cierre"}
    )
    assert rejected.status == "rejected" and "esperar al cierre" in rejected.outcome.summary
    assert writes.orderpoints == [] and writes.rfqs == {}
    assert runs.runs[paused.run_id]["status"] == "rejected"


async def test_model_never_touches_the_numbers(make_agent: Any, chat: ScriptedChatClient) -> None:
    """The guard at graph level: lines before and after explain differ only in explanation."""
    agent = make_agent()
    _script(chat, ["CBEA-LHN", "LODC-XDN", "990-011-007"])
    paused = await agent.run(InventoryPlanningTask(kind="daily_plan", case_id="plan_5"))
    snapshot = await agent.snapshot("plan_5")
    assert paused.proposal is not None
    for stored, line in zip(snapshot["lines"], paused.proposal.lines, strict=True):
        assert stored["rop"] == line.rop and stored["order_qty"] == line.order_qty


@pytest.mark.parametrize("kind", ["review_product", "what_if"])
def test_tasks_need_products(kind: str) -> None:
    with pytest.raises(ValueError):
        InventoryPlanningTask(kind=kind, case_id="c")  # type: ignore[arg-type]


async def test_empty_catalogue_fails_cleanly(make_agent: Any, data: Any) -> None:
    data.products_.clear()
    agent = make_agent()
    result = await agent.run(InventoryPlanningTask(kind="daily_plan", case_id="plan_6"))
    assert result.status == "failed" and "no plannable products" in result.outcome.summary
