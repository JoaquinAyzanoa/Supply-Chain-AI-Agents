"""The weekly run on fakes: history in, scores and scorecards out, writes after approval."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from sc_core.graph import ApprovalGateway, memory_checkpointer
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import SupplierPerformanceTask
from supplier_performance.agent import SupplierPerformanceAgent
from supplier_performance.graph import Deps, build_graph
from supplier_performance.metrics import score_supplier
from supplier_performance.nodes.steps import ScorecardText
from supplier_performance.testing import FakePerformancePorts, hidraulica_history
from tests.unit.graph.toy import FakeApprovalPorts

TODAY = date(2026, 9, 14)


@pytest.fixture
def ports() -> FakePerformancePorts:
    p = FakePerformancePorts()
    p.suppliers = [(45, "Proveedor Hidraulica")]
    p.histories[45] = hidraulica_history(late_lines=1)
    return p


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def approval_ports() -> FakeApprovalPorts:
    return FakeApprovalPorts()


@pytest.fixture
def make_agent(
    ports: FakePerformancePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> Any:
    def factory(**overrides: Any) -> SupplierPerformanceAgent:
        gateway = ApprovalGateway(
            approval_ports,
            agent_name="supplier_performance",
            callback_url="http://supplier_performance:8000/approvals/callback",
            callback_secret="s",
            approver_user_id=2,
            deadline_days=2,
        )
        kwargs: dict[str, Any] = {
            "ports": ports,
            "chat": chat,
            "approvals": gateway,
            "langfuse": LangfuseCfg(enabled=False),
            "today": lambda: TODAY,
        }
        kwargs.update(overrides)
        return SupplierPerformanceAgent(
            build_graph(Deps(**kwargs), memory_checkpointer()), model="fake-model", ports=ports
        )

    return factory


async def test_weekly_run_scores_writes_the_scorecard_and_applies_after_approval(
    make_agent: Any,
    ports: FakePerformancePorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    chat.responses.append(
        ScorecardText(
            scorecard="Proveedor Hidraulica delivers on time five times out of six and answers in "
            "about six hours; one late line this period; prices stable.",
            trends=["one late line this period"],
        )
    )
    agent = make_agent()
    paused = await agent.run(SupplierPerformanceTask(kind="weekly_scorecard", case_id="c_score1"))
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101
    assert paused.period_start == date(2025, 9, 19) and paused.period_end == TODAY
    [score] = paused.scores
    assert score.partner_name == "Proveedor Hidraulica" and score.otif == round(5 / 6, 4)
    assert score.scorecard is not None and score.scorecard.startswith(
        "Proveedor Hidraulica delivers"
    )
    assert score.trends == ["one late line this period"]
    facts = chat.last_prompt_text()
    assert "OTIF (on time and in full): 83% over 6 lines" in facts and "Score:" in facts
    assert ports.observations_saved == 6 and ports.saved_runs[0][0] == paused.run_id
    [created] = approval_ports.created
    assert created["kind"] == "supplier_score" and created["res_id"] is None
    [review] = approval_ports.reviews  # the To-Do hangs on the approval itself
    assert review["res_model"] == "sc.approval" and review["res_id"] == 101
    assert created["summary"] == (
        "Weekly supplier scorecards to 2026-09-14: 1 supplier(s), 1 with changes to watch"
    )
    assert created["payload"]["scores"][0]["partner_id"] == 45
    assert ports.applied == []  # nothing written to Odoo before the decision

    done = await agent.resume(
        "c_score1", {"approval_id": 101, "status": "approved", "resolved_by": "Ana"}
    )
    assert done.status == "applied" and done.applied is not None and done.applied.partners == 1
    assert ports.applied == [{"run_id": paused.run_id, "partners": [45], "approval_id": 101}]
    assert "1 supplier(s)" in done.outcome.summary and "planning parameter" in done.outcome.summary
    assert ports.runs[paused.run_id]["status"] == "applied"


async def test_previous_run_feeds_the_trend_flags(
    make_agent: Any, ports: FakePerformancePorts, chat: ScriptedChatClient
) -> None:
    ports.previous[45] = score_supplier(hidraulica_history(late_lines=0))
    ports.histories[45] = hidraulica_history(late_lines=4, replies_hours=(30.0, 40.0, 50.0))
    chat.responses.append(
        ScorecardText(
            scorecard="Deliveries slipped this period; four of six lines late.", trends=[]
        )
    )
    paused = await make_agent().run(
        SupplierPerformanceTask(kind="weekly_scorecard", case_id="c_score2", partner_ids=[45])
    )
    [score] = paused.scores
    assert any(f.startswith("OTIF down") for f in score.trends)
    assert any(f.startswith("replies slower") for f in score.trends)
    assert "Changes since the previous run" in chat.last_prompt_text()


async def test_no_activity_means_no_run(
    make_agent: Any, ports: FakePerformancePorts, approval_ports: FakeApprovalPorts
) -> None:
    ports.suppliers = []
    done = await make_agent().run(
        SupplierPerformanceTask(kind="weekly_scorecard", case_id="c_score3")
    )
    assert done.status == "no_action" and "nothing to score" in done.outcome.summary
    assert approval_ports.created == [] and ports.saved_runs == []


async def test_rejected_run_writes_nothing(
    make_agent: Any, ports: FakePerformancePorts, chat: ScriptedChatClient
) -> None:
    chat.responses.append(
        ScorecardText(scorecard="Steady supplier, one late line, replies within a day.", trends=[])
    )
    agent = make_agent()
    await agent.run(SupplierPerformanceTask(kind="weekly_scorecard", case_id="c_score4"))
    done = await agent.resume(
        "c_score4",
        {"approval_id": 101, "status": "rejected", "resolved_by": "Ana", "reason": "not this week"},
    )
    assert done.status == "rejected" and "not this week" in done.outcome.summary
    assert ports.applied == []
