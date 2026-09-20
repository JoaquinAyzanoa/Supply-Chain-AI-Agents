"""Learning from decisions: what a resolution records, and what calibration proposes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from director.desk.learning import (
    CalibrationJob,
    DecisionFeedback,
    FeedbackRecorder,
    MemoryFeedbackStore,
    MemorySuggestionStore,
    calibrate,
    edit_summary,
    group_stats,
    payload_notes,
)
from director.testing import MemoryApprovalsGateway
from sc_core.schema.autonomy import AutonomyPolicy, AutonomyRule, RuleConditions
from sc_core.schema.events import ScheduledTick

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def row(
    approval_id: int,
    kind: str = "send_email",
    *,
    partner_id: int | None = 8,
    status: str = "approved",
    edited: bool = False,
    notes: dict[str, object] | None = None,
    days_ago: int = 1,
) -> DecisionFeedback:
    return DecisionFeedback(
        approval_id=approval_id,
        kind=kind,
        partner_id=partner_id,
        status=status,
        edited=edited,
        edit_notes=notes or {},
        facts={"partner_id": partner_id, "partner_name": "Proveedor Hidraulica"}
        if partner_id
        else {},
        resolved_at=NOW - timedelta(days=days_ago),
        seconds_to_decide=600.0,
    )


def test_edit_summary_knows_each_kind() -> None:
    assert edit_summary("send_email", {}, {"subject": "Better"}) == (True, ["subject"], {})
    assert edit_summary("send_email", {}, None) == (False, [], {})
    changes = {
        "changes": [{"po_line_id": 1}, {"po_line_id": 2}, {"po_line_id": 3, "needs_review": True}]
    }
    assert edit_summary("po_change", changes, {"accepted_line_ids": [1]}) == (
        True,
        ["accepted_line_ids"],
        {"dropped_lines": 1},
    )
    assert edit_summary("po_change", changes, {"accepted_line_ids": [1, 2]})[0] is False
    plan = {"lines": [{"line_id": "r:1"}, {"line_id": "r:2"}]}
    edited, fields, notes = edit_summary(
        "planning_run",
        plan,
        {
            "accepted_line_ids": ["r:1"],
            "edits": {"r:1": {"order_qty": 3}},
            "params": {"r:1": {"service_level": 0.97}},
        },
    )
    assert edited and fields == ["accepted_line_ids", "edits", "params"]
    assert notes == {"dropped_lines": 1, "lines_edited": 1, "params_set": 1}
    assert edit_summary("vendor_bill", {}, None) == (False, [], {})


def test_payload_notes_keep_the_size_of_a_bill_variance() -> None:
    payload = {
        "verdict": "hold",
        "lines": [
            {"po_price": 100.0, "invoice_price": 102.4},
            {"po_price": 50.0, "invoice_price": 50.0},
            {"po_price": None, "invoice_price": 9.0},
        ],
    }
    assert payload_notes("vendor_bill", payload) == {"max_variance_pct": 2.4, "verdict": "hold"}
    assert payload_notes("send_email", payload) == {}


def test_group_stats_counts_outcomes_per_kind_and_supplier() -> None:
    rows = [
        row(1),
        row(2, edited=True),
        row(3, status="rejected"),
        row(4, partner_id=9),
        row(5, "po_change"),
    ]
    by_kind = {s.kind: s for s in group_stats(rows, by_partner=False)}
    assert by_kind["send_email"].n == 4 and by_kind["send_email"].unchanged == 2
    assert by_kind["send_email"].edited == 1 and by_kind["send_email"].rejected == 1
    assert by_kind["send_email"].median_minutes == 10.0
    per_supplier = {(s.kind, s.partner_id): s.n for s in group_stats(rows, by_partner=True)}
    assert per_supplier[("send_email", 8)] == 3 and per_supplier[("send_email", 9)] == 1


def test_calibration_suggests_a_rule_after_ten_unchanged_approvals_unless_already_automatic() -> (
    None
):
    rows = [row(i) for i in range(1, 11)]
    [suggestion] = calibrate(rows, AutonomyPolicy(), days=90)
    assert suggestion.kind == "autonomy_rule" and suggestion.key == "autonomy:send_email:8"
    assert "Proveedor Hidraulica" in suggestion.title
    rule = AutonomyRule.model_validate(suggestion.proposal["rule"])
    assert (
        rule.kind == "send_email" and rule.when.partner_ids == [8] and rule.level == "auto_notice"
    )
    assert suggestion.evidence["n"] == 10

    assert calibrate(rows[:9], AutonomyPolicy(), days=90) == []  # nine is not ten
    assert calibrate([*rows[:9], row(10, edited=True)], AutonomyPolicy(), days=90) == []
    already = AutonomyPolicy(
        rules=[
            AutonomyRule(
                id="hid", kind="send_email", when=RuleConditions(partner_ids=[8]), level="auto"
            )
        ]
    )
    assert calibrate(rows, already, days=90) == []


def test_calibration_suggests_a_tolerance_and_flags_rejections() -> None:
    held = [
        row(i, "vendor_bill", partner_id=None, notes={"verdict": "hold", "max_variance_pct": v})
        for i, v in enumerate([1.2, 2.4, 0.8, 1.9, 2.05], start=1)
    ]
    [tolerance] = calibrate(held, AutonomyPolicy(), days=90)
    assert tolerance.kind == "setting" and tolerance.proposal == {
        "setting": "invoice_price_tolerance_pct",
        "value": 2.4,
    }
    assert "2.40 %" in tolerance.detail
    one_rejected = [
        *held[:4],
        row(
            5,
            "vendor_bill",
            partner_id=None,
            status="rejected",
            notes={"verdict": "hold", "max_variance_pct": 9.0},
        ),
    ]
    assert calibrate(one_rejected, AutonomyPolicy(), days=90) == []

    rejected = [
        row(i, "po_change", status="rejected" if i % 2 else "approved") for i in range(1, 7)
    ]
    [attention] = calibrate(rejected, AutonomyPolicy(), days=90)
    assert attention.kind == "attention" and attention.evidence == {
        "n": 6,
        "rejected": 3,
        "days": 90,
    }
    assert attention.proposal == {}


async def test_calibration_job_backfills_odoo_decisions_then_upserts_suggestions() -> None:
    approvals = MemoryApprovalsGateway()
    for i in range(1, 11):
        approvals.seed(
            i,
            kind="send_email",
            summary=f"Send reminder {i}",
            payload={"facts": {"partner_id": 8, "partner_name": "Proveedor Hidraulica"}},
            status="approved",
        )
    approvals.seed(11, kind="send_email", summary="pending one", payload={})
    feedback, suggestions = MemoryFeedbackStore(), MemorySuggestionStore()
    job = CalibrationJob(
        recorder=FeedbackRecorder(approvals, feedback),
        feedback=feedback,
        suggestions=suggestions,
        policy=AutonomyPolicy().provider(),
    )
    out = await job.run(
        "calibration",
        ScheduledTick(
            event_id="t1",
            source="scheduler",
            case_id="c",
            job_id="calibration",
            run_id="r1",
            scheduled_at=NOW,
        ),
    )
    assert out["backfilled"] == 10 and out["rows"] == 10
    assert out["suggestions"] == ["autonomy:send_email:8"]
    [open_one] = await suggestions.open()
    assert open_one.kind == "autonomy_rule" and open_one.id == 1
    # a second run finds nothing new to backfill and keeps the same open row
    again = await job.run(
        "calibration",
        ScheduledTick(
            event_id="t2",
            source="scheduler",
            case_id="c",
            job_id="calibration",
            run_id="r2",
            scheduled_at=NOW,
        ),
    )
    assert again["backfilled"] == 0 and [s.id for s in await suggestions.open()] == [1]
    assert (
        await job.run(
            "other",
            ScheduledTick(
                event_id="t3",
                source="scheduler",
                case_id="c",
                job_id="other",
                run_id="r3",
                scheduled_at=NOW,
            ),
        )
    )["status"] == "not_implemented"
