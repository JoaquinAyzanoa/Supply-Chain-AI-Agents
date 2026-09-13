"""Playbooks on fakes: time travel through a late order, idempotent resume, fallbacks."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from director.agents import AgentProxy, Agents
from director.escalation import MemoryEscalator
from director.playbooks import MemoryPlaybookStore, PlaybookEngine, load_playbooks
from director.playbooks.model import Playbook
from director.policies import PoFacts
from director.store import MemoryCaseStore
from sc_core.a2a import AgentReply
from sc_core.a2a.testing import FakeAgentCaller

from .helpers import agent_reply

START = date(2026, 9, 14)


class Clock:
    """Today and now, moved by the test."""

    def __init__(self) -> None:
        self.today = START

    def date(self) -> date:
        return self.today

    def now(self) -> datetime:
        return datetime.combine(self.today, datetime.min.time(), tzinfo=UTC).replace(hour=9)

    def travel(self, days: int) -> None:
        self.today = self.today + timedelta(days=days)


class Facts:
    """The order's facts as the follow-up job would gather them; the test edits them."""

    def __init__(self, facts: PoFacts) -> None:
        self.rows = {facts.po_name: facts}

    async def facts_for(self, po_name: str, today: date) -> PoFacts | None:
        return self.rows.get(po_name)

    def set(self, po_name: str, **changes: Any) -> None:
        self.rows[po_name] = self.rows[po_name].model_copy(update=changes)


def late_po(name: str = "P00077") -> PoFacts:
    return PoFacts(
        po_id=77,
        po_name=name,
        partner_id=8,
        state="purchase",
        date_planned=START - timedelta(days=7),
        receipt_status="pending",
        last_outbound_at=None,
        last_inbound_at=None,
    )


def build(
    facts: Facts, clock: Clock, agent: FakeAgentCaller, *, with_sourcing: bool = False
) -> tuple[PlaybookEngine, MemoryCaseStore, MemoryEscalator, MemoryPlaybookStore]:
    cases, escalator = MemoryCaseStore(), MemoryEscalator()
    proxies: dict[str, AgentProxy] = {}
    if with_sourcing:
        proxies["sourcing"] = AgentProxy("sourcing", agent)
    agents = Agents(supplier_comms=AgentProxy("supplier_comms", agent), others=proxies)
    store = MemoryPlaybookStore(now=clock.now)
    engine = PlaybookEngine(
        store=store,
        cases=cases,
        agents=agents,
        escalator=escalator,
        facts=facts,
        today=clock.date,
        now=clock.now,
    )
    return engine, cases, escalator, store


def test_every_shipped_playbook_loads_and_names_known_conditions() -> None:
    playbooks = load_playbooks()
    assert set(playbooks) >= {
        "late_order",
        "silent_rfq",
        "receipt_variance",
        "quote_round",
        "new_supplier_onboarding",
    }
    late = playbooks["late_order"]
    assert [s.id for s in late.steps] == [
        "ask_eta",
        "wait_reply",
        "chase",
        "wait_again",
        "alternate",
    ]
    assert late.done_when == "received"
    assert late.steps[1].kind == "wait" and late.steps[1].until == "reply_received"
    with pytest.raises(ValueError, match="unknown condition"):
        Playbook.model_validate(
            {"name": "x", "title": "x", "steps": [{"id": "a", "when": "nope", "action": "close"}]}
        )
    with pytest.raises(ValueError, match="one of agent, wait or action"):
        Playbook.model_validate({"name": "x", "title": "x", "steps": [{"id": "a"}]})


async def test_a_late_order_goes_from_eta_request_to_alternate_sourcing_over_days() -> None:
    clock, agent = Clock(), FakeAgentCaller()
    facts = Facts(late_po())
    engine, cases, escalator, store = build(facts, clock, agent)
    agent.replies.append(
        agent_reply("request_eta", "pb_1_ask_eta", "sent", "asked", po_name="P00077")
    )

    # day 0: the follow-up job starts the playbook; the ETA request goes out and the run waits
    run = await engine.start(
        "late_order", po_name="P00077", partner_id=8, started_by="po_followups"
    )
    assert run.status == "waiting" and run.step_index == 1
    assert run.due_at == clock.now() + timedelta(days=2)
    assert json.loads(agent.sent[0].task_json)["kind"] == "request_eta"
    facts.set("P00077", last_outbound_at=clock.today)  # what mail activity would show
    position = engine.position(run)
    assert position.step_label == "Wait two days for a reply"
    assert position.next_steps[0] == "Chase once more, firmly"
    case = list(cases.cases.values())[0]
    assert case.po_name == "P00077" and case.kind == "eta"
    assert [e.kind for e in cases.case_events][:3] == ["playbook", "task_sent", "result"]

    # day 1: nothing is due; the hourly tick moves nothing
    clock.travel(1)
    assert (await engine.tick())["moved"] == []
    assert (await engine.counts())["late_order"] == {"wait_reply": 1}

    # day 2: still no reply -> the chase goes out, then another wait
    clock.travel(1)
    agent.replies.append(agent_reply("follow_up", "pb_1_chase", "sent", "chased", po_name="P00077"))
    assert (await engine.tick())["moved"] == [1]
    run = (await store.get(1)) or run
    assert run.status == "waiting" and run.step_index == 3
    chase = json.loads(agent.sent[1].task_json)
    assert chase["kind"] == "follow_up" and "Second request" in chase["notes"]
    assert chase["days_silent"] == 2

    # day 4: still silent -> the sourcing agent is not deployed: the fallback escalates
    clock.travel(2)
    facts.set("P00077", last_outbound_at=START + timedelta(days=2))
    await engine.tick()
    run = (await store.get(1)) or run
    assert run.status == "done" and run.summary is not None
    [call] = escalator.calls
    assert "sourcing is not deployed yet" in call["reason"]
    assert "a person has it now" in run.summary
    assert call["details"] == {"playbook": "late_order", "step": "alternate"}
    steps = [(s.step_id, s.status) for s in await store.steps(1)]
    assert steps == [
        ("ask_eta", "done"),
        ("wait_reply", "waiting"),
        ("wait_reply", "done"),
        ("chase", "done"),
        ("wait_again", "waiting"),
        ("wait_again", "done"),
        ("alternate", "escalated"),
    ]
    assert (await cases.get(case.case_id)).status == "escalated"  # type: ignore[union-attr]


async def test_a_reply_ends_the_wait_early_and_a_receipt_closes_the_case() -> None:
    clock, agent = Clock(), FakeAgentCaller()
    facts = Facts(late_po())
    engine, cases, escalator, store = build(facts, clock, agent)
    agent.replies.append(
        agent_reply("request_eta", "pb_1_ask_eta", "sent", "asked", po_name="P00077")
    )
    run = await engine.start("late_order", po_name="P00077", partner_id=8)
    facts.set("P00077", last_outbound_at=clock.today)

    # the supplier answers the next day: the wait ends early and there is nothing to chase
    clock.travel(1)
    facts.set("P00077", last_inbound_at=clock.today)
    moved = await engine.on_po_event("P00077")
    assert moved == [1]
    run = (await store.get(1)) or run
    # every remaining step is guarded by no_reply: the reply flow takes it from here
    assert run.status == "done" and escalator.calls == []
    assert [(s.step_id, s.status) for s in await store.steps(1)][-3:] == [
        ("chase", "skipped"),
        ("wait_again", "skipped"),
        ("alternate", "skipped"),
    ]


async def test_a_received_order_closes_and_an_approval_pauses_the_run() -> None:
    clock, agent = Clock(), FakeAgentCaller()
    facts = Facts(late_po())
    engine, cases, escalator, store = build(facts, clock, agent)
    # the ETA request needs a person this time
    agent.replies.append(
        agent_reply(
            "request_eta",
            "pb_1_ask_eta",
            "awaiting_approval",
            "draft",
            approval_id=9,
            po_name="P00077",
        )
    )
    run = await engine.start("late_order", po_name="P00077", partner_id=8)
    assert run.status == "waiting_approval" and run.step_index == 0
    assert engine.position(run).if_rejected is not None
    assert (await engine.tick())["moved"] == []  # still a person's turn

    # the person approved (the case moved on) and the goods arrived: the run closes the case
    case = list(cases.cases.values())[0]
    await cases.update(case.case_id, status="done")
    facts.set("P00077", receipt_status="full")
    await engine.tick()
    run = (await store.get(1)) or run
    assert run.status == "done" and "received" in (run.summary or "").lower()
    assert (await cases.get(case.case_id)).status == "done"  # type: ignore[union-attr]
    # one playbook per order: starting again returns nothing new while active, new when done
    again = await engine.start("late_order", po_name="P00077", partner_id=8)
    assert again.id == 2


async def test_with_the_sourcing_agent_deployed_the_alternate_step_runs_it() -> None:
    clock, agent = Clock(), FakeAgentCaller()
    facts = Facts(late_po())
    engine, cases, escalator, store = build(facts, clock, agent, with_sourcing=True)
    agent.replies.append(
        agent_reply("request_eta", "pb_1_ask_eta", "sent", "asked", po_name="P00077")
    )
    await engine.start("late_order", po_name="P00077", partner_id=8)
    facts.set("P00077", last_outbound_at=clock.today)
    clock.travel(2)
    agent.replies.append(agent_reply("follow_up", "pb_1_chase", "sent", "chased", po_name="P00077"))
    await engine.tick()
    clock.travel(2)
    # the sourcing agent's own result contract comes with S4: a bare completion is
    # read as "nothing to change" and the run ends without an escalation
    agent.replies.append(AgentReply(status="completed", text=""))
    await engine.tick()
    run = await store.get(1)
    assert run is not None and run.status == "done"
    sent = json.loads(agent.sent[2].task_json)
    assert sent["kind"] == "alternate_source" and sent["partner_id"] == 8
    assert escalator.calls == []
    last = (await store.steps(1))[-1]
    assert (last.step_id, last.status, last.detail["outcome"]) == ("alternate", "done", "no_action")
