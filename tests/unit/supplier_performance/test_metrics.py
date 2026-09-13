"""Metrics on a synthetic history: what a sourcing manager would compute by hand."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sc_core.schema.a2a import SupplierScore
from supplier_performance.metrics import (
    Weights,
    lead_time_days,
    otif,
    price_cv,
    promise_drift_days,
    quality_rate,
    response_hours_median,
    score_supplier,
    trends_against,
    trimmed,
)
from supplier_performance.models import MailPair, ReceivedLine, SupplierHistory
from supplier_performance.testing import hidraulica_history


def test_otif_counts_on_time_and_in_full_lines() -> None:
    assert otif(hidraulica_history(late_lines=1)) == (round(5 / 6, 4), 6)
    assert otif(hidraulica_history(late_lines=0, in_full=False)) == (0.5, 6)  # 3 short lines
    assert otif(hidraulica_history(late_lines=0).model_copy(update={"lines": []})) == (None, 0)


def test_lead_time_is_the_mean_and_std_of_confirmed_to_received() -> None:
    mean, sigma, n = lead_time_days(hidraulica_history(late_lines=2))
    assert n == 6 and mean == round((13 + 13 + 9 * 4) / 6, 2) and sigma is not None and sigma > 0
    assert trimmed([float(i) for i in range(40)]) == [float(i) for i in range(2, 38)]
    assert trimmed([3.0, 1.0, 2.0]) == [1.0, 2.0, 3.0]  # too few to trim


def test_promise_drift_is_per_order_against_the_first_promise() -> None:
    drift, orders = promise_drift_days(hidraulica_history(late_lines=2))
    # orders P00020 (two late lines, +3) and P00021, P00022 (on time, -1)
    assert orders == 3 and drift == round((3 - 1 - 1) / 3, 2)
    none, n = promise_drift_days(
        hidraulica_history().model_copy(
            update={
                "lines": [
                    line.model_copy(update={"promised_date": None})
                    for line in hidraulica_history().lines
                ]
            }
        )
    )
    assert none is None and n == 0


def test_response_time_is_the_median_of_answered_emails() -> None:
    assert response_hours_median(hidraulica_history()) == (6.0, 3)  # the unanswered one is ignored
    only_silence = SupplierHistory(
        partner_id=1,
        partner_name="x",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 2, 1),
        mails=[MailPair(sent_at=datetime(2026, 1, 5, tzinfo=UTC))],
    )
    assert response_hours_median(only_silence) == (None, 0)


def test_quality_and_price_stability() -> None:
    history = hidraulica_history().model_copy(update={"discrepancy_lines": 3})
    assert quality_rate(history) == (0.5, 6)
    cv, products = price_cv(hidraulica_history())
    assert products == 1 and cv is not None and 0 < cv < 0.02  # one product with two prices


def test_score_weighs_only_what_has_data() -> None:
    full = score_supplier(hidraulica_history(late_lines=0))
    assert 80 < full.score <= 100 and full.otif == 1.0 and full.samples["lines"] == 6
    late = score_supplier(hidraulica_history(late_lines=3))
    assert late.score < full.score
    bare = SupplierHistory(
        partner_id=2, partner_name="new", period_start=date(2026, 1, 1), period_end=date(2026, 9, 1)
    )
    assert score_supplier(bare).score == 0.0 and score_supplier(bare).samples["lines"] == 0
    # only mails known: the score comes from the reply time alone, renormalised to 100
    quick = bare.model_copy(
        update={
            "mails": [
                MailPair(
                    sent_at=datetime(2026, 3, 1, tzinfo=UTC),
                    replied_at=datetime(2026, 3, 1, tzinfo=UTC) + timedelta(hours=2),
                )
            ]
        }
    )
    scored = score_supplier(quick)
    assert scored.response_hours_median == 2.0 and 90 < scored.score <= 100
    heavy = score_supplier(
        hidraulica_history(late_lines=3),
        weights=Weights(otif=0.9, lead_time=0.1, promise=0, response=0, quality=0),
    )
    assert heavy.score < late.score  # OTIF dominates and it is poor


def test_trends_flag_worse_lead_time_otif_replies_and_quality() -> None:
    previous = score_supplier(hidraulica_history(late_lines=0))
    worse = hidraulica_history(late_lines=4, replies_hours=(20.0, 40.0, 60.0)).model_copy(
        update={
            "discrepancy_lines": 2,
            "lines": [
                line.model_copy(update={"received_at": line.received_at + timedelta(days=6)})
                for line in hidraulica_history(late_lines=4).lines
            ],
        }
    )
    current = score_supplier(worse, previous=previous)
    flags = current.trends
    assert any(f.startswith("lead time up") for f in flags)
    assert any(f.startswith("OTIF down") for f in flags)
    assert any(f.startswith("replies slower") for f in flags)
    assert any(f.startswith("more receipt problems") for f in flags)
    assert trends_against(current, None) == []
    assert isinstance(current, SupplierScore)


def test_single_line_history_has_no_spread() -> None:
    one = hidraulica_history(late_lines=0).model_copy(
        update={"lines": hidraulica_history(late_lines=0).lines[:1]}
    )
    mean, sigma, n = lead_time_days(one)
    assert n == 1 and sigma == 0.0 and mean == 9.0
    assert isinstance(one.lines[0], ReceivedLine)
