"""The autonomy policy: first match wins, missing facts never widen, raises are detected."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sc_core.infra.runtime_settings import _parse
from sc_core.schema.autonomy import (
    ActionFacts,
    AutonomyPolicy,
    AutonomyRule,
    RuleConditions,
)
from sc_core.schema.runtime_settings import RuntimeSettings


def rule(
    id: str, kind: str = "send_email", level: str = "auto_notice", **when: object
) -> AutonomyRule:
    return AutonomyRule(id=id, kind=kind, level=level, when=RuleConditions(**when))  # type: ignore[arg-type]


def test_first_matching_rule_decides_and_the_default_is_approve() -> None:
    policy = AutonomyPolicy(
        rules=[
            rule("trusted", partner_ids=[8], level="auto"),
            rule("routine", email_kinds=["request_eta", "follow_up"]),
        ]
    )
    hidraulica = ActionFacts(partner_id=8, email_kind="rfq")
    assert policy.decide("send_email", hidraulica).rule_id == "trusted"
    assert policy.decide("send_email", hidraulica).level == "auto"
    routine = policy.decide("send_email", ActionFacts(partner_id=9, email_kind="follow_up"))
    assert routine.level == "auto_notice" and routine.rule_id == "routine"
    nothing = policy.decide("send_email", ActionFacts(partner_id=9, email_kind="rfq"))
    assert nothing.level == "approve" and nothing.rule_id is None
    assert policy.decide("po_change", hidraulica).level == "approve"  # kind does not match
    assert not policy.decide("send_email").automatic  # no facts at all: never


def test_a_condition_without_its_fact_does_not_match() -> None:
    policy = AutonomyPolicy(rules=[rule("small-bills", kind="vendor_bill", amount_max=500)])
    assert policy.decide("vendor_bill", ActionFacts(amount=120)).automatic
    assert not policy.decide("vendor_bill", ActionFacts(amount=800)).automatic
    assert not policy.decide("vendor_bill", ActionFacts()).automatic  # amount unknown
    scored = AutonomyPolicy(rules=[rule("good", supplier_score_min=70, confidence_min=0.8)])
    assert scored.decide("send_email", ActionFacts(supplier_score=75, confidence=0.9)).automatic
    assert not scored.decide("send_email", ActionFacts(supplier_score=75)).automatic


def test_some_kinds_are_never_automated_and_disabled_rules_are_skipped() -> None:
    policy = AutonomyPolicy(rules=[rule("all", kind="*", level="auto")])
    assert policy.decide("send_email", ActionFacts()).level == "auto"
    assert policy.decide("escalation", ActionFacts()).level == "approve"
    assert policy.decide("autonomy_change", ActionFacts()).level == "approve"
    off = AutonomyPolicy(
        rules=[rule("all", kind="*", level="auto").model_copy(update={"enabled": False})]
    )
    assert off.decide("send_email").level == "approve"


def test_raises_over_lists_only_what_widens_autonomy() -> None:
    current = AutonomyPolicy(
        rules=[rule("a", partner_ids=[8]), rule("b", kind="vendor_bill", amount_max=500)]
    )
    same = AutonomyPolicy(rules=list(current.rules))
    assert same.raises_over(current) == []
    lowered = AutonomyPolicy(rules=[rule("a", partner_ids=[8], level="approve")])
    assert lowered.raises_over(current) == []  # dropping b and lowering a: no raise
    widened = AutonomyPolicy(
        rules=[
            rule("a", partner_ids=[8, 9]),  # more suppliers
            rule("b", kind="vendor_bill", amount_max=500, level="auto"),  # higher level
            rule("c", email_kinds=["follow_up"]),  # new automatic rule
            rule("d", level="approve"),  # new but not automatic
        ]
    )
    assert widened.raises_over(current) == ["a", "b", "c"]
    shorter = AutonomyPolicy(
        rules=[rule("a", partner_ids=[8]).model_copy(update={"revert_hours": 1})]
    )
    assert shorter.raises_over(current) == ["a"]


def test_rules_are_validated() -> None:
    with pytest.raises(ValidationError, match="unknown approval kind"):
        AutonomyRule(id="x", kind="nope")
    with pytest.raises(ValidationError, match="unique"):
        AutonomyPolicy(rules=[rule("x"), rule("x")])
    with pytest.raises(ValidationError):
        AutonomyRule(id="Bad Id")


def test_legacy_settings_become_rules_on_read() -> None:
    policy = AutonomyPolicy.from_legacy([45], ["request_eta"], 1500.0)
    assert [r.id for r in policy.rules] == [
        "legacy-auto-send-partners",
        "legacy-auto-send-kinds",
        "legacy-bill-under-amount",
    ]
    assert policy.decide("send_email", ActionFacts(partner_id=45)).automatic
    assert policy.decide("vendor_bill", ActionFacts(amount=100, confidence=1.0)).automatic
    assert not policy.decide("vendor_bill", ActionFacts(amount=100, confidence=0.0)).automatic
    # a settings row saved before phase 11 keeps its auto-send behaviour
    fallback = RuntimeSettings(autonomy=AutonomyPolicy.from_legacy(bill_auto_approve_amount=900))
    parsed = _parse({"auto_send_kinds": ["follow_up"], "rfq_no_reply_days": [2]}, fallback=fallback)
    assert parsed.rfq_no_reply_days == [2]
    assert parsed.autonomy.decide("send_email", ActionFacts(email_kind="follow_up")).automatic
    assert parsed.autonomy.rule("legacy-bill-under-amount") is not None
    assert AutonomyPolicy.from_legacy().rules == []
