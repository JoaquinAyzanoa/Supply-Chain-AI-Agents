"""The planner's memory: a rule change rejected twice is held; parameters kept from a
review become the product's own."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from inventory_planning.domain.policy import MemoryHoldStore, MemoryParamsStore
from inventory_planning.domain.policy.holds import next_hold
from inventory_planning.infra.runs import MemoryRunStore
from inventory_planning.testing import FakeWritePorts
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import InventoryPlanningTask

from .test_graph_daily import _script

TODAY = date(2026, 9, 14)  # the conftest's AS_OF


def test_two_rejections_inside_the_window_hold_the_product() -> None:
    first = next_hold(None, 1, approval_id=10, today=TODAY)
    assert first.rejections == 1 and first.held_until is None
    second = next_hold(first, 1, approval_id=11, today=TODAY + timedelta(days=20))
    assert second.rejections == 2 and second.held_until == TODAY + timedelta(days=50)
    # a rejection long after the first starts counting again
    late = next_hold(first, 1, approval_id=12, today=TODAY + timedelta(days=90))
    assert (
        late.rejections == 1
        and late.held_until is None
        and late.first_rejected_on == TODAY + timedelta(days=90)
    )


async def test_a_rule_change_rejected_twice_is_not_proposed_again(
    make_agent: Any, chat: ScriptedChatClient, writes: FakeWritePorts, runs: MemoryRunStore
) -> None:
    holds = MemoryHoldStore()
    params = MemoryParamsStore()

    async def plan(case_id: str) -> Any:
        agent = make_agent(holds=holds, params=params)
        _script(chat, ["CBEA-LHN", "LODC-XDN", "990-011-007"])
        return agent, await agent.run(InventoryPlanningTask(kind="daily_plan", case_id=case_id))

    # twice, the planner drops the seal kit's rule change (overstock) and keeps the rest
    for case_id in ("hold_1", "hold_2"):
        agent, paused = await plan(case_id)
        lines = {ln.product_ref: ln for ln in paused.proposal.lines}
        kit = lines["990-011-007"]
        assert kit.action == "update_rule" and kit.held_until is None
        accepted = [
            ln.line_id for ref, ln in lines.items() if ref != "990-011-007" and ln.action != "none"
        ]
        done = await agent.resume(
            case_id,
            {"approval_id": 101, "status": "approved", "details": {"accepted_line_ids": accepted}},
        )
        assert done.status == "applied"
    assert holds.rows[4].rejections == 2 and holds.rows[4].held_until == TODAY + timedelta(days=30)

    # the third plan shows the kit as held and proposes no rule for it
    _, third = await plan("hold_3")
    kit = {ln.product_ref: ln for ln in third.proposal.lines}["990-011-007"]
    assert kit.held_until == TODAY + timedelta(days=30) and kit.action == "none"
    assert kit.exception == "overstock"  # the situation is still shown, just not acted on


async def test_parameters_kept_from_a_review_become_the_products_own(
    make_agent: Any, chat: ScriptedChatClient, writes: FakeWritePorts
) -> None:
    params = MemoryParamsStore()
    agent = make_agent(params=params, holds=MemoryHoldStore())
    _script(chat, ["CBEA-LHN", "LODC-XDN", "990-011-007"])
    paused = await agent.run(InventoryPlanningTask(kind="daily_plan", case_id="keep_1"))
    lines = {ln.product_ref: ln for ln in paused.proposal.lines}
    valve = lines["CBEA-LHN"]
    done = await agent.resume(
        "keep_1",
        {
            "approval_id": 101,
            "status": "approved",
            "details": {
                "accepted_line_ids": [valve.line_id],
                "params": {
                    valve.line_id: {"service_level": 0.98, "review_period_days": 14, "ignored": 1}
                },
            },
        },
    )
    assert done.status == "applied"
    kept = params.rows[valve.product_id]
    assert kept.service_level == 0.98 and kept.review_period_days == 14 and kept.source == "planner"
