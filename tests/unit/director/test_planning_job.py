"""The daily planning tick and planner replies through the director."""

from __future__ import annotations

import json
from datetime import date

from director.agents import AgentProxy, Agents
from director.escalation import MemoryEscalator
from director.handlers.planning import JobDispatcher, PlanningJob
from director.jobs import NoJobs
from director.store import MemoryCaseStore
from director.testing import MemoryDirectorModule
from director.workflow import outcome_from_reply
from sc_core.a2a import AgentReply
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.schema import events as ev
from sc_core.schema.a2a import InventoryPlanningResult, Outcome

from .helpers import tick

TODAY = date(2026, 9, 14)


def _planner_reply(status: str, thread_id: str, approval_id: int | None = None) -> AgentReply:
    result = InventoryPlanningResult(
        kind="daily_plan",
        case_id=thread_id,
        run_id="run_p1",
        outcome=Outcome(status=status, summary="plan propuesto", approval_id=approval_id),  # type: ignore[arg-type]
    )
    return AgentReply(
        status="input_required" if status == "awaiting_approval" else "completed",
        text=result.model_dump_json(),
    )


async def test_daily_plan_opens_a_planning_case_and_waits_for_approval() -> None:
    cases, escalator = MemoryCaseStore(), MemoryEscalator()
    planner = FakeAgentCaller()
    planner.replies.append(_planner_reply("awaiting_approval", "plan_2026-09-14_run_1", 12))
    agents = Agents(
        supplier_comms=AgentProxy("supplier_comms", FakeAgentCaller()),
        others={"inventory_planning": AgentProxy("inventory_planning", planner)},
    )
    job = PlanningJob(cases=cases, agents=agents, escalator=escalator, today=lambda: TODAY)
    summary = await job.run("inventory_planning", tick("inventory_planning", "run_1"))
    assert summary["status"] == "ok" and summary["plan_status"] == "awaiting_approval"
    assert summary["approval_id"] == 12
    task = json.loads(planner.sent[0].task_json)
    assert task["kind"] == "daily_plan" and task["as_of"] == "2026-09-14"
    assert task["case_id"] == "plan_2026-09-14_run_1"
    [case] = cases.cases.values()
    assert (
        case.kind == "planning"
        and case.status == "awaiting_approval"
        and case.agent == "inventory_planning"
    )
    assert planner.sent[0].case_id == case.case_id
    assert [e.kind for e in cases.case_events] == ["task_sent", "result", "approval_requested"]
    assert escalator.calls == []


async def test_planner_failure_escalates_the_planning_case() -> None:
    cases, escalator = MemoryCaseStore(), MemoryEscalator()
    planner = FakeAgentCaller()
    planner.replies.append(_planner_reply("failed", "plan_x"))
    agents = Agents(
        supplier_comms=AgentProxy("supplier_comms", FakeAgentCaller()),
        others={"inventory_planning": AgentProxy("inventory_planning", planner)},
    )
    job = PlanningJob(cases=cases, agents=agents, escalator=escalator, today=lambda: TODAY)
    summary = await job.run("inventory_planning", tick("inventory_planning", "run_2"))
    assert summary["plan_status"] == "failed"
    [case] = cases.cases.values()
    assert case.status == "escalated" and len(escalator.calls) == 1


async def test_dispatcher_routes_by_job_id() -> None:
    cases = MemoryCaseStore()
    dispatcher = JobDispatcher({"po_followups": NoJobs()})
    assert await dispatcher.run("po_followups", tick("po_followups")) == {
        "job": "po_followups",
        "status": "not_implemented",
    }
    assert await dispatcher.run("supplier_performance", tick("supplier_performance")) == {
        "job": "supplier_performance",
        "status": "not_implemented",
    }
    assert cases.cases == {}


async def test_orderpoint_event_reaches_the_planner_through_the_workflow() -> None:
    planner = FakeAgentCaller()
    planner.replies.append(_planner_reply("no_action", "odoo_orderpoint_3"))
    module = MemoryDirectorModule()
    module.deps.agents.others["inventory_planning"] = AgentProxy("inventory_planning", planner)
    event = ev.OdooOrderpointTriggered(
        source="odoo",
        case_id="odoo_orderpoint_3",
        orderpoint_id=3,
        product_id=11,
        product_code="CBEA-LHN",
        qty_to_order=12,
    )
    result = await module.orchestrator.handle(event)
    task = json.loads(planner.sent[0].task_json)
    assert task["kind"] == "review_product" and task["product_ids"] == [11]
    [case] = module.cases.cases.values()
    assert case.kind == "planning" and case.status == "done"
    assert result["updates"][0]["detail"]["agent"] == "inventory_planning"


def test_outcome_from_reply_understands_both_contracts() -> None:
    from director.store import Case

    case = Case(case_id="c", kind="planning", status="open")
    planner = outcome_from_reply(
        case, "inventory_planning", "daily_plan", "t", _planner_reply("awaiting_approval", "t", 5)
    )
    assert planner.status == "awaiting_approval" and planner.approval_id == 5
    assert planner.run_id == "run_p1" and planner.sent_message_id is None
    garbage = outcome_from_reply(
        case, "inventory_planning", "daily_plan", "t", AgentReply(status="completed", text="{}")
    )
    assert garbage.status == "no_action"
