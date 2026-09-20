"""A counter-offer never leaves the cap, never reaches the quote, and stops after the rounds."""

from __future__ import annotations

import pytest

from sc_core.shared.errors import ValidationFailed
from sourcing.negotiation import check_edited_offer, floor_for, plan_offer


def test_target_from_last_paid_within_the_cap() -> None:
    plan = plan_offer(
        current_price=110.0,
        last_paid=100.0,
        competing=[],
        target_override=None,
        cap_pct=10.0,
        round_no=1,
        max_rounds=2,
    )
    assert plan is not None
    assert plan.floor_price == 99.0 and plan.target_price == 100.0 and plan.offered_price == 100.0
    assert plan.basis == "our last paid price of 100.00"


def test_a_target_below_the_floor_is_raised_to_the_floor() -> None:
    plan = plan_offer(
        current_price=110.0,
        last_paid=80.0,
        competing=[85.0],
        target_override=None,
        cap_pct=10.0,
        round_no=1,
        max_rounds=2,
    )
    assert plan is not None
    assert plan.target_price == 80.0 and plan.offered_price == 99.0  # the cap wins
    assert plan.offered_price >= floor_for(110.0, 10.0)


def test_the_best_competing_price_is_the_basis_when_it_beats_history() -> None:
    plan = plan_offer(
        current_price=110.0,
        last_paid=108.0,
        competing=[104.16, 114.24],
        target_override=None,
        cap_pct=10.0,
        round_no=1,
        max_rounds=2,
    )
    assert plan is not None and plan.offered_price == 104.16
    assert plan.basis == "a competing price of 104.16"


def test_a_buyers_target_is_used_as_given() -> None:
    plan = plan_offer(
        current_price=110.0,
        last_paid=100.0,
        competing=[],
        target_override=102.5,
        cap_pct=10.0,
        round_no=1,
        max_rounds=2,
    )
    assert plan is not None and plan.offered_price == 100.0  # the cheaper evidence still wins
    plan = plan_offer(
        current_price=110.0,
        last_paid=None,
        competing=[],
        target_override=102.5,
        cap_pct=10.0,
        round_no=1,
        max_rounds=2,
    )
    assert plan is not None and plan.offered_price == 102.5 and plan.basis == "the buyer's target"


def test_no_evidence_means_a_modest_opening_ask() -> None:
    plan = plan_offer(
        current_price=100.0,
        last_paid=None,
        competing=[],
        target_override=None,
        cap_pct=10.0,
        round_no=1,
        max_rounds=2,
    )
    assert (
        plan is not None and plan.offered_price == 95.0 and plan.basis == "a standard opening ask"
    )


def test_nothing_to_negotiate_when_the_quote_is_already_at_target() -> None:
    assert (
        plan_offer(
            current_price=100.0,
            last_paid=100.0,
            competing=[],
            target_override=None,
            cap_pct=10.0,
            round_no=1,
            max_rounds=2,
        )
        is None
    )
    # evidence that is worse than the quote is not a reason to ask for less
    assert (
        plan_offer(
            current_price=100.0,
            last_paid=None,
            competing=[120.0],
            target_override=None,
            cap_pct=10.0,
            round_no=1,
            max_rounds=2,
        )
        is None
    )


def test_rounds_are_limited() -> None:
    assert (
        plan_offer(
            current_price=110.0,
            last_paid=100.0,
            competing=[],
            target_override=None,
            cap_pct=10.0,
            round_no=3,
            max_rounds=2,
        )
        is None
    )


def test_a_person_may_edit_within_the_limits_only() -> None:
    assert check_edited_offer(101.5, current_price=110.0, floor_price=99.0) == 101.5
    with pytest.raises(ValidationFailed, match="below the floor"):
        check_edited_offer(90.0, current_price=110.0, floor_price=99.0)
    with pytest.raises(ValidationFailed, match="not below the quoted price"):
        check_edited_offer(110.0, current_price=110.0, floor_price=99.0)


def test_a_zero_price_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        plan_offer(
            current_price=0.0,
            last_paid=None,
            competing=[],
            target_override=None,
            cap_pct=10.0,
            round_no=1,
            max_rounds=2,
        )


def test_the_basis_is_said_in_the_language_the_buyer_reads() -> None:
    plan = plan_offer(
        current_price=110.0,
        last_paid=108.0,
        competing=[104.16, 114.24],
        target_override=None,
        cap_pct=10.0,
        round_no=1,
        max_rounds=2,
        language="es",
    )
    assert plan is not None and plan.offered_price == 104.16
    assert plan.basis == "un precio de la competencia, 104.16"
