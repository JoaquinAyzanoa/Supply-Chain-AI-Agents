"""The approval gateway asks the policy: automatic levels record a decision and a row."""

from __future__ import annotations

from typing import Any

from sc_core.graph import ApprovalGateway, ApprovalRequest, MemoryAutoActions
from sc_core.schema.autonomy import ActionFacts, AutonomyPolicy, AutonomyRule, RuleConditions
from tests.unit.graph.toy import FakeApprovalPorts

POLICY = AutonomyPolicy(
    rules=[
        AutonomyRule(
            id="trusted-dates",
            kind="po_change",
            when=RuleConditions(partner_ids=[8], change_days_max=7, confidence_min=0.8),
            level="auto_notice",
            revert_hours=48,
        ),
        AutonomyRule(
            id="routine-mail",
            kind="send_email",
            when=RuleConditions(email_kinds=["request_eta"]),
            level="auto",
        ),
    ]
)
CATCH_ALL = AutonomyPolicy(rules=[AutonomyRule(id="everything", kind="*", level="auto")])


def gateway(
    ports: FakeApprovalPorts, store: MemoryAutoActions, policy: AutonomyPolicy = POLICY
) -> ApprovalGateway:
    return ApprovalGateway(
        ports,
        agent_name="toy",
        callback_url="http://toy/approvals/callback",
        callback_secret="s",
        approver_user_id=2,
        deadline_days=2,
        policy=policy.provider(),
        auto_actions=store,
    )


def state(**extra: Any) -> dict[str, Any]:
    return {"case_id": "case_1", "run_id": "run_1", **extra}


async def test_a_revertible_change_runs_alone_with_a_window() -> None:
    ports, store = FakeApprovalPorts(), MemoryAutoActions()
    req = ApprovalRequest(
        kind="po_change",
        summary="Move 2 lines of P00074 by 3 days",
        payload={"po_name": "P00074", "changes": []},
        po_id=74,
        facts=ActionFacts(partner_id=8, change_days=3, confidence=0.9),
        revert={
            "po_id": 74,
            "lines": [{"line_id": 1, "field": "date_planned", "before": "2026-09-25"}],
        },
    )
    out = await gateway(ports, store).prepare(state(), "change", req)
    [decision] = out["approvals"]
    assert decision["status"] == "approved" and decision["resolved_by"] == "policy:trusted-dates"
    assert decision["level"] == "auto_notice" and decision["rule_id"] == "trusted-dates"
    assert ports.created == []  # nothing in Odoo to approve
    [row] = store.rows
    assert row.kind == "po_change" and row.po_name == "P00074" and row.partner_id == 8
    assert row.revert == req.revert and row.revert_until is not None
    assert row.level == "auto_notice" and row.rule_id == "trusted-dates" and row.agent == "toy"


async def test_facts_outside_the_rule_still_need_a_person_and_carry_the_facts() -> None:
    ports, store = FakeApprovalPorts(), MemoryAutoActions()
    req = ApprovalRequest(
        kind="po_change",
        summary="Move a line by 20 days",
        payload={"po_name": "P00074"},
        po_id=74,
        facts=ActionFacts(partner_id=8, change_days=20, confidence=0.9),
    )
    out = await gateway(ports, store).prepare(state(), "change", req)
    assert "pending_approvals" in out and store.rows == []
    [created] = ports.created
    assert created["payload"]["facts"]["change_days"] == 20  # the preview replays these


async def test_auto_level_is_logged_without_a_revert_and_forced_requests_are_never_automatic() -> (
    None
):
    ports, store = FakeApprovalPorts(), MemoryAutoActions()
    mail = ApprovalRequest(
        kind="send_email",
        summary="Ask for the date",
        payload={"po_name": "P00074", "html_body": "<p>secret</p>"},
        po_id=74,
        facts=ActionFacts(partner_id=8, email_kind="request_eta"),
    )
    out = await gateway(ports, store).prepare(state(), "send", mail)
    assert out["approvals"][0]["level"] == "auto" and store.rows[0].revert_until is None
    assert "html_body" not in store.rows[0].payload  # never a mail body in the feed

    forced = mail.model_copy(update={"force_approval": True})
    out = await gateway(ports, store).prepare(state(), "send", forced)
    assert "pending_approvals" in out and len(ports.created) == 1

    escalation = ApprovalRequest(kind="escalation", summary="help", payload={}, po_id=74)
    out = await gateway(ports, store, CATCH_ALL).prepare(state(), "esc", escalation)
    assert "pending_approvals" in out  # never automated, even by the catch-all rule


async def test_without_a_policy_the_legacy_flag_still_works() -> None:
    ports, store = FakeApprovalPorts(), MemoryAutoActions()
    plain = ApprovalGateway(
        ports,
        agent_name="toy",
        callback_url=None,
        callback_secret=None,
        approver_user_id=2,
        deadline_days=2,
        auto_actions=store,
    )
    req = ApprovalRequest(
        kind="send_email", summary="x", payload={}, po_id=1, auto_approve=True, auto_reason="ok"
    )
    out = await plain.prepare(state(), "send", req)
    assert out["approvals"][0]["resolved_by"] == "auto" and store.rows[0].rule_id is None
