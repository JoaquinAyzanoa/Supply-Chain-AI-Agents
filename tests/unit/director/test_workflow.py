"""The orchestration workflow on in-memory doubles and a fake agent."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from director.conversations import MemoryConversationLookup
from director.escalation import MemoryEscalator
from director.inbox import MemoryEventResults
from director.jobs import JobRunner
from director.store import MemoryCaseStore
from director.testing import memory_deps
from director.workflow import Orchestrator, case_status_for
from sc_core.a2a import AgentReply
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.schema import events as ev
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import ExternalServiceError

from .helpers import agent_reply, classified_reply, linked, logistics_reply, po_confirmed, tick


@pytest.fixture
def cases() -> MemoryCaseStore:
    return MemoryCaseStore()


@pytest.fixture
def agent() -> FakeAgentCaller:
    return FakeAgentCaller()


@pytest.fixture
def escalator() -> MemoryEscalator:
    return MemoryEscalator()


@pytest.fixture
def results() -> MemoryEventResults:
    return MemoryEventResults()


@pytest.fixture
def conversations() -> MemoryConversationLookup:
    return MemoryConversationLookup({"AAMkSent": "conv-out"})


@pytest.fixture
def orchestrator(
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
    results: MemoryEventResults,
    conversations: MemoryConversationLookup,
) -> Orchestrator:
    deps = memory_deps(
        cases=cases, supplier_comms=agent, escalator=escalator, conversations=conversations
    )
    return Orchestrator(deps, results)


def _kinds(cases: MemoryCaseStore, case_id: str) -> list[str]:
    return [e.kind for e in cases.case_events if e.case_id == case_id]


async def test_linked_mail_runs_handle_inbound_and_updates_the_case(
    orchestrator: Orchestrator,
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    results: MemoryEventResults,
    escalator: MemoryEscalator,
) -> None:
    agent.replies.append(
        agent_reply(
            "handle_inbound", "case_msg1", "awaiting_approval", "changes proposed", approval_id=101
        )
    )
    event = linked()
    result = await orchestrator.handle(event)

    assert len(agent.sent) == 1
    task = json.loads(agent.sent[0].task_json)
    assert task["kind"] == "handle_inbound" and task["graph_message_id"] == "AAMk1"
    assert task["case_id"] == "case_msg1"  # the agent thread is the event's case id
    [case] = cases.cases.values()
    assert agent.sent[0].case_id == case.case_id  # the case is the Langfuse session
    assert case.status == "awaiting_approval" and case.summary == "changes proposed"
    assert case.kind == "inbound" and case.po_name == "P00015" and case.conversation_id == "conv1"
    assert case.agent == "supplier_comms"
    assert _kinds(cases, case.case_id) == [
        "event_received",
        "task_sent",
        "result",
        "approval_requested",
    ]
    assert results.results[event.event_id]["case_id"] == case.case_id
    [update] = results.results[event.event_id]["updates"]
    assert update["status"] == "awaiting_approval" and update["detail"]["approval_id"] == 101
    assert result == results.results[event.event_id]
    assert escalator.calls == []


async def test_failed_result_escalates_once(
    orchestrator: Orchestrator,
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
) -> None:
    agent.replies.append(agent_reply("handle_inbound", "case_msg1", "failed", "Odoo write failed"))
    await orchestrator.handle(linked())
    [case] = cases.cases.values()
    assert case.status == "escalated"
    assert len(escalator.calls) == 1 and escalator.calls[0]["reason"] == "Odoo write failed"
    assert escalator.calls[0]["details"]["task"] == "handle_inbound"
    assert _kinds(cases, case.case_id)[-1] == "escalated"


async def test_agent_unreachable_is_a_failed_outcome(
    orchestrator: Orchestrator,
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
    results: MemoryEventResults,
) -> None:
    agent.replies.append(ExternalServiceError("agent down", service="a2a"))
    event = linked()
    await orchestrator.handle(event)
    [case] = cases.cases.values()
    assert case.status == "escalated" and "unreachable" in (case.summary or "")
    assert escalator.calls[0]["details"]["error"]["code"] == "external_service_error"
    assert results.results[event.event_id]["updates"][0]["detail"]["status"] == "failed"


async def test_reply_that_is_not_a_result_is_a_failure(
    orchestrator: Orchestrator, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    agent.replies.append(AgentReply(status="failed", text="boom"))
    await orchestrator.handle(linked())
    [case] = cases.cases.values()
    assert case.status == "escalated" and case.summary == "boom"


async def test_po_confirmed_sends_the_order_and_learns_the_thread(
    orchestrator: Orchestrator, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    agent.replies.append(
        agent_reply(
            "send_po",
            "odoo_po_66_purchase",
            "sent",
            "order sent",
            sent_message_id="AAMkSent",
            po_name="P00066",
        )
    )
    await orchestrator.handle(po_confirmed())
    task = json.loads(agent.sent[0].task_json)
    assert task["kind"] == "send_po" and task["po_name"] == "P00066"
    [case] = cases.cases.values()
    assert case.kind == "eta" and case.status == "done" and case.partner_id == 9
    assert case.conversation_id == "conv-out"

    # the supplier's reply on that thread attaches to the same case
    agent.replies.append(
        agent_reply("handle_inbound", "case_msg9", "applied", "ETA confirmed", po_name="P00066")
    )
    reply = linked(case_id="case_msg9", po_name="P00066", conversation_id="conv-out")
    await orchestrator.handle(reply)
    assert len(cases.cases) == 2, "a done case never receives new work"


async def test_paused_case_collects_the_next_reply_on_its_thread(
    orchestrator: Orchestrator, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    agent.replies.append(
        agent_reply("handle_inbound", "case_msg1", "awaiting_approval", "proposal", approval_id=5)
    )
    await orchestrator.handle(linked())
    agent.replies.append(agent_reply("handle_inbound", "case_msg2", "no_action", "nothing new"))
    await orchestrator.handle(linked(case_id="case_msg2", graph_message_id="AAMk2"))
    [case] = cases.cases.values()
    assert [t.case_id for t in agent.sent] == [case.case_id, case.case_id]
    threads = [json.loads(t.task_json)["case_id"] for t in agent.sent]
    assert threads == ["case_msg1", "case_msg2"], "each run has its own agent thread"
    assert case.status == "done"


async def test_unknown_sender_escalates_without_calling_an_agent(
    orchestrator: Orchestrator,
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
) -> None:
    event = ev.InboundMailUnlinked(
        source="mail_sync", case_id="case_msg3", graph_message_id="AAMk3", sender_address="a@b.c"
    )
    await orchestrator.handle(event)
    assert agent.sent == []
    [case] = cases.cases.values()
    assert case.kind == "unlinked" and case.status == "escalated" and case.po_name is None
    assert len(escalator.calls) == 1 and "unknown sender" in escalator.calls[0]["reason"]


async def test_record_only_event_closes_a_fresh_case(
    orchestrator: Orchestrator, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    # a receipt without an order: nothing to reconcile, so it is only recorded
    receipt = ev.OdooReceiptValidated(
        source="odoo", case_id="odoo_pick_5", picking_id=5, picking_name="WH/IN/00005"
    )
    await orchestrator.handle(receipt)
    [case] = cases.cases.values()
    assert case.kind == "receipt" and case.status == "done"
    notes = [e.payload for e in cases.case_events if e.kind == "note"]
    assert notes and "WH/IN/00005" in notes[0]["text"]
    assert agent.sent == []


async def test_run_finished_finds_the_case_by_thread(
    orchestrator: Orchestrator, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    agent.replies.append(
        agent_reply("handle_inbound", "case_msg1", "awaiting_approval", "proposal", approval_id=5)
    )
    await orchestrator.handle(linked())
    [case] = cases.cases.values()
    finished = ev.AgentRunFinished(
        source="supplier_comms",
        case_id="case_msg1",
        agent="supplier_comms",
        thread_id="case_msg1",
        run_id="run_1",
        task_kind="handle_inbound",
        status="applied",
        summary="changes applied after approval",
        po_name=None,  # even without a PO the thread finds the case
    )
    await orchestrator.handle(finished)
    assert len(cases.cases) == 1
    assert cases.cases[case.case_id].status == "done"
    assert cases.cases[case.case_id].summary and "run run_1" in cases.cases[case.case_id].summary


async def test_approval_resolved_mirrors_and_expired_escalates(
    orchestrator: Orchestrator,
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
) -> None:
    agent.replies.append(
        agent_reply("handle_inbound", "case_msg1", "awaiting_approval", "proposal", approval_id=5)
    )
    await orchestrator.handle(linked())
    [case] = cases.cases.values()
    approved = ev.OdooApprovalResolved(
        source="odoo",
        case_id="odoo_appr_5",
        approval_id=5,
        kind="po_change",
        status="approved",
        thread_id="case_msg1",
        po_name="P00015",
        resolved_by="admin",
    )
    await orchestrator.handle(approved)
    assert (
        cases.cases[case.case_id].status == "awaiting_approval"
    )  # the agent's own event closes it
    assert _kinds(cases, case.case_id)[-1] == "note"
    expired = approved.model_copy(
        update={"status": "expired", "event_id": "evt_x", "case_id": "odoo_appr_5b"}
    )
    await orchestrator.handle(expired)
    assert cases.cases[case.case_id].status == "escalated" and len(escalator.calls) == 1


async def test_scheduler_tick_runs_the_job(
    cases: MemoryCaseStore, results: MemoryEventResults
) -> None:
    class RecordingJobs:
        def __init__(self) -> None:
            self.runs: list[tuple[str, str]] = []

        async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]:
            self.runs.append((job_id, tick.run_id))
            return {"status": "ok", "tasks": 2}

    jobs = RecordingJobs()
    assert isinstance(jobs, JobRunner)
    orchestrator = Orchestrator(memory_deps(cases=cases, jobs=jobs), results)
    event = tick("po_followups", "run_7")
    await orchestrator.handle(event)
    assert jobs.runs == [("po_followups", "run_7")]
    [case] = cases.cases.values()
    assert case.kind == "eta" and case.status == "done" and case.po_name is None
    assert results.results[event.event_id]["updates"][0]["detail"] == {"status": "ok", "tasks": 2}


async def test_unroutable_event_is_recorded_not_raised(
    orchestrator: Orchestrator, cases: MemoryCaseStore, results: MemoryEventResults
) -> None:
    class Custom(ev.BaseEvent):
        pass

    event = Custom(type="custom", source="x", case_id="c")
    result = await orchestrator.handle(event)
    assert result["status"] == "unroutable" and cases.cases == {}
    assert results.results[event.event_id] == result


async def test_per_agent_semaphore_bounds_concurrency(cases: MemoryCaseStore) -> None:
    class SlowCaller:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0

        async def send(self, task_json: str, *, case_id: str, metadata: Any = None) -> AgentReply:
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.02)
            self.active -= 1
            thread = json.loads(task_json)["case_id"]
            return agent_reply("handle_inbound", thread, "no_action", "ok")

    caller = SlowCaller()
    deps = memory_deps(cases=cases, supplier_comms=caller, max_concurrent=2)
    orchestrator = Orchestrator(deps, MemoryEventResults())
    events = [
        linked(case_id=f"case_msg{i}", graph_message_id=f"AAMk{i}", po_name=f"P{i:05d}")
        for i in range(6)
    ]
    await asyncio.gather(*(orchestrator.handle(e) for e in events))
    assert caller.peak == 2 and deps.agents.supplier_comms.max_in_flight == 2
    assert len(cases.cases) == 6 and all(c.status == "done" for c in cases.cases.values())


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("sent", "done"),
        ("applied", "done"),
        ("no_action", "done"),
        ("awaiting_approval", "awaiting_approval"),
        ("rejected", "rejected"),
        ("escalated", "escalated"),
        ("failed", "failed"),
        ("something_else", "failed"),
    ],
)
def test_case_status_mapping(outcome: str, expected: str) -> None:
    assert case_status_for(outcome) == expected


async def test_resolved_escalation_closes_the_case(
    orchestrator: Orchestrator,
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
) -> None:
    agent.replies.append(agent_reply("handle_inbound", "case_msg1", "failed", "Odoo write failed"))
    await orchestrator.handle(linked())
    [case] = cases.cases.values()
    assert case.status == "escalated"
    resolved = ev.OdooApprovalResolved(
        source="odoo",
        case_id="odoo_appr_9",
        approval_id=9,
        kind="escalation",
        status="approved",
        thread_id=case.case_id,  # escalations are created on the case id
        po_name="P00015",
        resolved_by="admin",
    )
    await orchestrator.handle(resolved)
    assert len(cases.cases) == 1 and cases.cases[case.case_id].status == "done"
    rejected = resolved.model_copy(update={"status": "rejected", "event_id": "evt_r"})
    # a done case never reopens: the decision is recorded as a new case's note
    await orchestrator.handle(rejected)
    assert cases.cases[case.case_id].status == "done"
    assert len(escalator.calls) == 1  # an escalation decision never escalates again


async def test_expired_escalation_stays_with_the_person(
    orchestrator: Orchestrator,
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
) -> None:
    agent.replies.append(agent_reply("handle_inbound", "case_msg1", "failed", "boom"))
    await orchestrator.handle(linked())
    [case] = cases.cases.values()
    expired = ev.OdooApprovalResolved(
        source="odoo",
        case_id="odoo_appr_9",
        approval_id=9,
        kind="escalation",
        status="expired",
        thread_id=case.case_id,
    )
    await orchestrator.handle(expired)
    assert cases.cases[case.case_id].status == "escalated" and len(escalator.calls) == 1


async def test_run_finished_after_approval_teaches_the_thread(
    orchestrator: Orchestrator, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    agent.replies.append(
        agent_reply(
            "send_po",
            "odoo_po_66_purchase",
            "awaiting_approval",
            "draft",
            approval_id=5,
            po_name="P00066",
        )
    )
    await orchestrator.handle(po_confirmed())
    [case] = cases.cases.values()
    assert case.conversation_id is None
    finished = ev.AgentRunFinished(
        source="supplier_comms",
        case_id="odoo_po_66_purchase",
        agent="supplier_comms",
        thread_id="odoo_po_66_purchase",
        run_id="run_1",
        task_kind="send_po",
        status="sent",
        summary="order sent",
        po_name="P00066",
        approval_id=5,
        sent_message_id="AAMkSent",
    )
    await orchestrator.handle(finished)
    updated = cases.cases[case.case_id]
    assert updated.status == "done" and updated.conversation_id == "conv-out"
    # the supplier's reply on that thread now attaches... to a new case, since this one is done,
    # but a reply on a still-open case would: check the attach rule with an open case
    agent.replies.append(
        agent_reply("handle_inbound", "case_msg9", "no_action", "ok", po_name="P00066")
    )
    await orchestrator.handle(
        linked(case_id="case_msg9", po_name="P00066", conversation_id="conv-out")
    )
    assert len(cases.cases) == 2


async def test_error_document_reply_becomes_a_readable_failure(
    orchestrator: Orchestrator, cases: MemoryCaseStore, agent: FakeAgentCaller
) -> None:
    doc = '{"code": "odoo_rpc_error", "message": "The method x does not exist", "traceback": "..."}'
    agent.replies.append(AgentReply(status="failed", text=doc))
    await orchestrator.handle(linked())
    [case] = cases.cases.values()
    assert case.summary == "supplier_comms failed: The method x does not exist"
    result = next(e for e in cases.case_events if e.kind == "result")
    assert result.payload["error"]["code"] == "odoo_rpc_error"


async def test_shipping_notice_goes_on_to_the_logistics_agent(
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
    results: MemoryEventResults,
    conversations: MemoryConversationLookup,
) -> None:
    """The supplier agent reads the email and says "shipping notice"; the director then
    asks the logistics agent to track it on the same case, without a second event."""
    logistics = FakeAgentCaller()
    orchestrator = Orchestrator(
        memory_deps(
            cases=cases,
            supplier_comms=agent,
            logistics=logistics,
            escalator=escalator,
            conversations=conversations,
        ),
        results,
    )
    agent.replies.append(classified_reply("handle_inbound", "case_msg1", "shipping_notice"))
    logistics.replies.append(
        logistics_reply(
            "track_shipment",
            "case_msg1_ship",
            "awaiting_approval",
            "arrival proposed",
            approval_id=77,
        )
    )
    event = linked()
    await orchestrator.handle(event)

    [sent] = logistics.sent
    task = json.loads(sent.task_json)
    assert task["kind"] == "track_shipment" and task["case_id"] == "case_msg1_ship"
    assert task["po_name"] == "P00015" and task["graph_message_id"] == "AAMk1"
    [case] = cases.cases.values()
    assert sent.case_id == case.case_id
    assert case.status == "awaiting_approval" and case.agent == "logistics"
    assert case.summary == "arrival proposed"
    assert _kinds(cases, case.case_id) == [
        "event_received",
        "task_sent",
        "result",
        "task_sent",
        "result",
        "approval_requested",
    ]
    updates = results.results[event.event_id]["updates"]
    assert [u["detail"]["agent"] for u in updates] == ["supplier_comms", "logistics"]
    assert escalator.calls == []
