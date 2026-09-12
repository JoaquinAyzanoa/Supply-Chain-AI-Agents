"""Follow-up policy (table-driven) and the daily job on fakes."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest

from director.agents import AgentProxy, Agents
from director.conversations import MemoryMailActivity
from director.escalation import MemoryEscalator
from director.handlers.followups import FollowUpJob, OrdersPort
from director.policies import Decision, FollowUpPolicy, PoFacts, decide
from director.store import MemoryCaseStore
from sc_core.a2a.testing import FakeAgentCaller
from sc_core.odoo.models import Approval, PurchaseOrder, Ref

from .helpers import agent_reply, tick

TODAY = date(2026, 9, 14)
POLICY = FollowUpPolicy()


def _facts(**overrides: Any) -> PoFacts:
    base: dict[str, Any] = {
        "po_id": 1,
        "po_name": "P00015",
        "partner_id": 7,
        "state": "draft",
        "last_outbound_at": date(2026, 9, 11),
    }
    return PoFacts(**{**base, **overrides})


def _d(
    rule: str, task: str | None = None, *, escalate: bool = False, days: int = 0
) -> dict[str, Any]:
    return {"rule": rule, "task": task, "escalate": escalate, "days": days}


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        # RFQ silence: nothing at 2 days, follow-up at 3, second at 7, escalate after
        (_facts(last_outbound_at=date(2026, 9, 12)), None),
        (_facts(last_outbound_at=date(2026, 9, 11)), _d("rfq_silent", "follow_up", days=3)),
        (_facts(last_outbound_at=date(2026, 9, 9), rules_fired=["rfq_silent"]), None),
        (
            _facts(last_outbound_at=date(2026, 9, 7), rules_fired=["rfq_silent"]),
            _d("rfq_silent", "follow_up", days=7),
        ),
        (
            _facts(last_outbound_at=date(2026, 9, 6), rules_fired=["rfq_silent", "rfq_silent"]),
            _d("rfq_unanswered", escalate=True, days=8),
        ),
        (
            _facts(last_outbound_at=date(2026, 9, 7), rules_fired=["rfq_silent", "rfq_silent"]),
            None,  # exactly 7 days: the second follow-up went out today
        ),
        (
            _facts(
                last_outbound_at=date(2026, 9, 6),
                rules_fired=["rfq_silent", "rfq_silent", "rfq_unanswered"],
            ),
            None,  # already escalated
        ),
        # a reply after our email means the inbound flow owns it
        (_facts(last_outbound_at=date(2026, 9, 1), last_inbound_at=date(2026, 9, 2)), None),
        (_facts(last_outbound_at=None), None),  # an RFQ we never emailed is not ours to chase
        # a case waiting for a human blocks everything
        (_facts(awaiting_human=True), None),
        # confirmed order past its date: request_eta at 1 day, escalate at 4
        (
            _facts(state="purchase", date_planned=date(2026, 9, 13), receipt_status="pending"),
            _d("po_late", "request_eta", days=1),
        ),
        (
            _facts(
                state="purchase",
                date_planned=date(2026, 9, 12),
                receipt_status="partial",
                rules_fired=["po_late:2026-09-12"],
            ),
            None,
        ),
        (
            _facts(
                state="purchase",
                date_planned=date(2026, 9, 10),
                receipt_status="pending",
                rules_fired=["po_late:2026-09-10"],
            ),
            _d("po_late_escalate", escalate=True, days=4),
        ),
        (_facts(state="purchase", date_planned=date(2026, 9, 10), receipt_status="full"), None),
        # due soon: ask for the date once, unless we already wrote
        (
            _facts(state="purchase", date_planned=date(2026, 9, 18), last_outbound_at=None),
            _d("eta_before_due", "request_eta", days=4),
        ),
        (
            _facts(state="purchase", date_planned=date(2026, 9, 14), last_outbound_at=None),
            _d("eta_before_due", "request_eta", days=0),
        ),
        (_facts(state="purchase", date_planned=date(2026, 9, 20), last_outbound_at=None), None),
        (_facts(state="purchase", date_planned=date(2026, 9, 18)), None),  # we wrote, waiting
        (
            _facts(
                state="purchase",
                date_planned=date(2026, 9, 18),
                last_outbound_at=None,
                rules_fired=["eta_before_due:2026-09-18"],
            ),
            None,
        ),
        # the date moved after we asked: the same rule may fire again for the new date
        (
            _facts(
                state="purchase",
                date_planned=date(2026, 9, 13),
                receipt_status="pending",
                rules_fired=["po_late:2026-09-05"],
            ),
            _d("po_late", "request_eta", days=1),
        ),
        (_facts(state="purchase", date_planned=None), None),
        (_facts(state="cancel"), None),
    ],
)
def test_policy_table(facts: PoFacts, expected: dict[str, Any] | None) -> None:
    decision = decide(facts, POLICY, TODAY)
    if expected is None:
        assert decision is None
    else:
        assert decision is not None and decision.po_name == "P00015"
        assert decision.model_dump(include=set(expected)) == expected
        assert decision.reason


def test_policy_from_settings() -> None:
    from sc_core.infra.settings import DirectorCfg

    policy = FollowUpPolicy.from_settings(DirectorCfg(rfq_no_reply_days=[2], po_late_days=[2, 3]))
    assert policy.rfq_no_reply_days == [2] and policy.po_late_days == [2, 3]
    silent = _facts(last_outbound_at=date(2026, 9, 12))
    assert decide(silent, policy, TODAY) == Decision(
        po_name="P00015",
        rule="rfq_silent",
        key="rfq_silent",
        task="follow_up",
        days=2,
        reason="no reply to the RFQ for 2 days (follow-up 1)",
    )


# --- the job -----------------------------------------------------------------------


def _po(
    po_id: int, name: str, state: str, planned: date | None, receipt: str | None = None
) -> PurchaseOrder:
    return PurchaseOrder(
        id=po_id,
        name=name,
        state=state,  # type: ignore[arg-type]
        partner_id=Ref(id=7, name="Proveedor"),
        date_planned=datetime.combine(planned, datetime.min.time(), tzinfo=UTC)
        if planned
        else None,
        receipt_status=receipt,  # type: ignore[arg-type]
    )


class FakeOrders:
    def __init__(self, orders: list[PurchaseOrder]) -> None:
        self.orders = orders

    async def late_open_orders(self, as_of: date) -> list[PurchaseOrder]:
        return [
            o
            for o in self.orders
            if o.state == "purchase"
            and o.date_planned
            and o.date_planned.date() < as_of
            and o.receipt_status != "full"
        ]

    async def open_due_within(self, as_of: date, days: int) -> list[PurchaseOrder]:
        return [
            o
            for o in self.orders
            if o.state == "purchase"
            and o.date_planned
            and 0 <= (o.date_planned.date() - as_of).days <= days
            and o.receipt_status != "full"
        ]

    async def by_names(self, names: Sequence[str]) -> list[PurchaseOrder]:
        return [o for o in self.orders if o.name in names]


@pytest.fixture
def seeded() -> tuple[FakeOrders, MemoryMailActivity]:
    orders = FakeOrders(
        [
            _po(1, "P00010", "draft", None),  # RFQ emailed 3 days ago, silent → follow_up
            _po(2, "P00011", "purchase", date(2026, 9, 12), "pending"),  # 2 days late → request_eta
            _po(3, "P00012", "purchase", date(2026, 9, 9), "pending"),  # 5 days late → escalate
            _po(
                4, "P00013", "purchase", date(2026, 9, 17), "pending"
            ),  # due in 3 days → request_eta
            _po(5, "P00014", "purchase", date(2026, 10, 30), "pending"),  # far away → nothing
            _po(6, "P00016", "draft", None),  # RFQ answered → nothing
        ]
    )
    mail = MemoryMailActivity(
        {
            "P00010": (date(2026, 9, 11), None),
            "P00016": (date(2026, 9, 1), date(2026, 9, 2)),
        }
    )
    assert isinstance(orders, OrdersPort)
    return orders, mail


def _job(
    seeded: tuple[FakeOrders, MemoryMailActivity],
    cases: MemoryCaseStore,
    agent: FakeAgentCaller,
    escalator: MemoryEscalator,
) -> FollowUpJob:
    orders, mail = seeded
    return FollowUpJob(
        policy=POLICY,
        orders=orders,
        mail=mail,
        cases=cases,
        agents=Agents(supplier_comms=AgentProxy("supplier_comms", agent)),
        escalator=escalator,
        today=lambda: TODAY,
    )


async def test_seeded_orders_yield_exactly_the_expected_tasks(
    seeded: tuple[FakeOrders, MemoryMailActivity],
) -> None:
    cases, agent, escalator = MemoryCaseStore(), FakeAgentCaller(), MemoryEscalator()
    agent.replies.extend(
        [
            agent_reply(
                "follow_up",
                "followup_p00010_2026-09-14",
                "awaiting_approval",
                "draft",
                approval_id=1,
                po_name="P00010",
            ),
            agent_reply(
                "request_eta", "followup_p00011_2026-09-14", "sent", "asked", po_name="P00011"
            ),
            agent_reply(
                "request_eta",
                "followup_p00013_2026-09-14",
                "awaiting_approval",
                "draft",
                approval_id=2,
                po_name="P00013",
            ),
        ]
    )
    job = _job(seeded, cases, agent, escalator)
    summary = await job.run("po_followups", tick("po_followups", "run_1"))

    assert summary["status"] == "ok" and summary["orders"] == 5  # P00014 is not due yet
    assert summary["tasks_sent"] == 3 and summary["escalated"] == 1
    sent = [
        (json.loads(t.task_json)["po_name"], json.loads(t.task_json)["kind"]) for t in agent.sent
    ]
    assert sent == [("P00010", "follow_up"), ("P00011", "request_eta"), ("P00013", "request_eta")]
    first = json.loads(agent.sent[0].task_json)
    assert first["days_silent"] == 3 and first["case_id"] == "followup_p00010_2026-09-14"
    assert [c["reason"] for c in escalator.calls] == [
        "5 days past the planned date without a receipt or a new ETA"
    ]
    by_po = {c.po_name: c for c in cases.cases.values()}
    assert by_po["P00010"].kind == "rfq" and by_po["P00010"].status == "awaiting_approval"
    assert by_po["P00011"].kind == "eta" and by_po["P00011"].status == "done"
    assert by_po["P00012"].status == "escalated"
    assert by_po["P00013"].status == "awaiting_approval"
    assert "P00014" not in by_po and "P00016" not in by_po
    rules = [e.payload["rule"] for e in cases.case_events if e.kind == "rule_fired"]
    assert rules == ["rfq_silent", "po_late", "po_late_escalate", "eta_before_due"]
    assert await cases.rules_fired("P00010") == ["rfq_silent"]
    assert await cases.rules_fired("P00011") == ["po_late:2026-09-12"]


async def test_second_run_the_same_day_sends_nothing_new(
    seeded: tuple[FakeOrders, MemoryMailActivity],
) -> None:
    cases, agent, escalator = MemoryCaseStore(), FakeAgentCaller(), MemoryEscalator()
    agent.replies.extend(
        [
            agent_reply("follow_up", "t1", "sent", "sent", po_name="P00010"),
            agent_reply("request_eta", "t2", "sent", "sent", po_name="P00011"),
            agent_reply("request_eta", "t3", "sent", "sent", po_name="P00013"),
        ]
    )
    job = _job(seeded, cases, agent, escalator)
    await job.run("po_followups", tick("po_followups", "run_1"))
    again = await job.run("po_followups", tick("po_followups", "run_2"))
    assert again["tasks_sent"] == 0 and again["escalated"] == 0
    assert len(agent.sent) == 3 and len(escalator.calls) == 1


async def test_agent_failure_escalates_that_order_only(
    seeded: tuple[FakeOrders, MemoryMailActivity],
) -> None:
    from sc_core.shared.errors import ExternalServiceError

    cases, agent, escalator = MemoryCaseStore(), FakeAgentCaller(), MemoryEscalator()
    agent.replies.extend(
        [
            ExternalServiceError("agent down", service="a2a"),
            agent_reply("request_eta", "t2", "sent", "sent", po_name="P00011"),
            agent_reply("request_eta", "t3", "sent", "sent", po_name="P00013"),
        ]
    )
    job = _job(seeded, cases, agent, escalator)
    summary = await job.run("po_followups", tick("po_followups", "run_1"))
    assert summary["tasks_sent"] == 3
    statuses = {c.po_name: c.status for c in cases.cases.values()}
    assert statuses["P00010"] == "escalated" and statuses["P00011"] == "done"
    assert {c["case_id"] for c in escalator.calls} == {
        c.case_id for c in cases.cases.values() if c.po_name in ("P00010", "P00012")
    }


async def test_other_jobs_are_not_implemented(
    seeded: tuple[FakeOrders, MemoryMailActivity],
) -> None:
    job = _job(seeded, MemoryCaseStore(), FakeAgentCaller(), MemoryEscalator())
    assert await job.run("inventory_planning", tick("inventory_planning")) == {
        "job": "inventory_planning",
        "status": "not_implemented",
    }


# --- stale approvals ------------------------------------------------------------------


class FakeApprovals:
    def __init__(self, approvals: list[Approval]) -> None:
        self.approvals = approvals
        self.reminded: list[tuple[int, int]] = []
        self.expired: list[tuple[int, str]] = []

    async def pending(self) -> list[Approval]:
        return [a for a in self.approvals if a.status == "pending"]

    async def remind(self, approval: Approval, *, days_pending: int) -> None:
        self.reminded.append((approval.id, days_pending))

    async def expire(self, approval_id: int, *, reason: str) -> None:
        self.expired.append((approval_id, reason))
        self.approvals = [
            a.model_copy(update={"status": "expired"}) if a.id == approval_id else a
            for a in self.approvals
        ]


def _approval(approval_id: int, thread_id: str, created: date) -> Approval:
    return Approval(
        id=approval_id,
        kind="send_email",
        summary="Enviar RFQ",
        status="pending",
        po_id=Ref(id=15, name="P00015"),
        thread_id=thread_id,
        create_date=datetime.combine(created, datetime.min.time()),
    )


async def test_stale_approvals_are_reminded_once_and_expired(
    seeded: tuple[FakeOrders, MemoryMailActivity],
) -> None:
    cases, agent, escalator = MemoryCaseStore(), FakeAgentCaller(), MemoryEscalator()
    for name, thread in (("P00010", "t-fresh"), ("P00011", "t-stale"), ("P00012", "t-old")):
        case, _ = await cases.attach_or_create(kind="rfq", po_name=name)
        await cases.add_event(case.case_id, "task_sent", {"task": "send_rfq", "thread_id": thread})
        await cases.update(case.case_id, status="awaiting_approval")
    approvals = FakeApprovals(
        [
            _approval(1, "t-fresh", date(2026, 9, 13)),  # 1 day: nothing
            _approval(2, "t-stale", date(2026, 9, 11)),  # 3 days: remind
            _approval(3, "t-old", date(2026, 9, 6)),  # 8 days: expire
            _approval(4, "t-unknown", date(2026, 9, 10)),  # no case: still reminded
        ]
    )
    orders, mail = seeded
    agent.replies.append(agent_reply("request_eta", "t", "sent", "sent", po_name="P00013"))
    job = FollowUpJob(
        policy=POLICY,
        orders=orders,
        mail=mail,
        cases=cases,
        agents=Agents(supplier_comms=AgentProxy("supplier_comms", agent)),
        escalator=escalator,
        approvals=approvals,
        today=lambda: TODAY,
    )
    summary = await job.run("po_followups", tick("po_followups", "run_1"))
    assert summary["approvals"] == {"reminded": [2, 4], "expired": [3]}
    assert approvals.reminded == [(2, 3), (4, 4)]
    assert approvals.expired[0][0] == 3 and "8 días" in approvals.expired[0][1]
    # the three orders wait for a human: only P00013 (due soon) got a task
    assert [json.loads(t.task_json)["po_name"] for t in agent.sent] == ["P00013"]

    again = await job.run("po_followups", tick("po_followups", "run_2"))
    assert again["approvals"] == {"reminded": [4], "expired": []}  # 2 was reminded already
    notes = [e.payload for e in cases.case_events if e.kind == "note"]
    assert [n["approval_id"] for n in notes] == [2, 3]


async def test_action_cap_defers_the_rest_to_the_next_run(
    seeded: tuple[FakeOrders, MemoryMailActivity],
) -> None:
    cases, agent, escalator = MemoryCaseStore(), FakeAgentCaller(), MemoryEscalator()
    agent.replies.extend(
        [
            agent_reply("follow_up", "t1", "sent", "sent", po_name="P00010"),
            agent_reply("request_eta", "t2", "sent", "sent", po_name="P00011"),
        ]
    )
    orders, mail = seeded
    job = FollowUpJob(
        policy=POLICY.model_copy(update={"max_actions_per_run": 2}),
        orders=orders,
        mail=mail,
        cases=cases,
        agents=Agents(supplier_comms=AgentProxy("supplier_comms", agent)),
        escalator=escalator,
        today=lambda: TODAY,
    )
    first = await job.run("po_followups", tick("po_followups", "run_1"))
    assert first["tasks_sent"] == 2 and first["escalated"] == 0
    assert first["skipped"] == ["P00012", "P00013"]
    assert await cases.rules_fired("P00012") == []  # nothing recorded: it fires next time

    agent.replies.append(agent_reply("request_eta", "t3", "sent", "sent", po_name="P00013"))
    second = await job.run("po_followups", tick("po_followups", "run_2"))
    assert second["escalated"] == 1 and second["tasks_sent"] == 1 and second["skipped"] == []


async def test_escalation_approvals_are_reminded_but_never_expired(
    seeded: tuple[FakeOrders, MemoryMailActivity],
) -> None:
    cases, agent, escalator = MemoryCaseStore(), FakeAgentCaller(), MemoryEscalator()
    old = _approval(7, "t-esc", date(2026, 9, 1)).model_copy(update={"kind": "escalation"})
    approvals = FakeApprovals([old])
    orders, mail = seeded
    agent.replies.extend(
        [
            agent_reply("follow_up", "t1", "sent", "sent", po_name="P00010"),
            agent_reply("request_eta", "t2", "sent", "sent", po_name="P00011"),
            agent_reply("request_eta", "t3", "sent", "sent", po_name="P00013"),
        ]
    )
    job = FollowUpJob(
        policy=POLICY,
        orders=orders,
        mail=mail,
        cases=cases,
        agents=Agents(supplier_comms=AgentProxy("supplier_comms", agent)),
        escalator=escalator,
        approvals=approvals,
        today=lambda: TODAY,
    )
    summary = await job.run("po_followups", tick("po_followups", "run_1"))
    assert summary["approvals"] == {"reminded": [7], "expired": []}
    assert approvals.expired == [] and approvals.reminded == [(7, 13)]
