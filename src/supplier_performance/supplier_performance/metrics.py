"""Supplier metrics, in code, from the history.

| Metric          | Formula                                                              |
|-----------------|----------------------------------------------------------------------|
| OTIF            | share of lines received on or before the promise and in full         |
| Lead time       | confirmed -> received per line, mean and std, 5% trimmed each side   |
| Promise drift   | mean days between the first promise and the final receipt, per order |
| Response time   | median hours from an email we sent to the supplier's next reply      |
| Quality         | discrepancy lines / received lines                                   |
| Price stability | mean coefficient of variation of unit prices per product             |

The score (0 to 100) weighs the components that have a sample; missing ones
are left out and the weights renormalised, so a new supplier is scored on
what is known, not punished for what is not.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import timedelta

from sc_core.schema.a2a import SupplierScore
from supplier_performance.models import SupplierHistory

EPSILON = 1e-6


@dataclass(frozen=True)
class Weights:
    """Share of the score each component carries; must sum to 1."""

    otif: float = 0.40
    lead_time: float = 0.15
    promise: float = 0.15
    response: float = 0.15
    quality: float = 0.15
    price: float = 0.0  # informational by default: price changes are a negotiation, not a fault

    def as_dict(self) -> dict[str, float]:
        return {
            "otif": self.otif,
            "lead_time": self.lead_time,
            "promise": self.promise,
            "response": self.response,
            "quality": self.quality,
            "price": self.price,
        }


@dataclass
class Components:
    """Each component on a 0..1 scale, ``None`` when there is no sample."""

    otif: float | None = None
    lead_time: float | None = None
    promise: float | None = None
    response: float | None = None
    quality: float | None = None
    price: float | None = None
    samples: dict[str, int] = field(default_factory=dict)


def trimmed(values: list[float], share: float = 0.05) -> list[float]:
    """Drop ``share`` of the values at each end when there are enough to matter (20+)."""
    if len(values) < 20:
        return sorted(values)
    cut = int(len(values) * share)
    return sorted(values)[cut : len(values) - cut]


def lead_time_days(history: SupplierHistory) -> tuple[float | None, float | None, int]:
    days = [
        max((line.received_at - line.confirmed_at) / timedelta(days=1), 0.0)
        for line in history.lines
    ]
    if not days:
        return None, None, 0
    kept = trimmed(days)
    mean = statistics.fmean(kept)
    sigma = statistics.pstdev(kept) if len(kept) > 1 else 0.0
    return round(mean, 2), round(sigma, 2), len(days)


def otif(history: SupplierHistory) -> tuple[float | None, int]:
    if not history.lines:
        return None, 0
    hits = 0
    for line in history.lines:
        on_time = line.promised_date is None or line.received_at.date() <= line.promised_date
        in_full = line.received + EPSILON >= line.ordered
        hits += int(on_time and in_full)
    return round(hits / len(history.lines), 4), len(history.lines)


def promise_drift_days(history: SupplierHistory) -> tuple[float | None, int]:
    """Per order: the last receipt against the first promise; positive means late."""
    by_order: dict[str, tuple[float, float]] = {}
    for line in history.lines:
        if line.promised_date is None:
            continue
        drift = (line.received_at.date() - line.promised_date).days
        first, last = by_order.get(line.po_name, (drift, drift))
        by_order[line.po_name] = (min(first, drift), max(last, drift))
    if not by_order:
        return None, 0
    return round(statistics.fmean(last for _, last in by_order.values()), 2), len(by_order)


def response_hours_median(history: SupplierHistory) -> tuple[float | None, int]:
    hours = [
        (pair.replied_at - pair.sent_at) / timedelta(hours=1)
        for pair in history.mails
        if pair.replied_at is not None and pair.replied_at >= pair.sent_at
    ]
    if not hours:
        return None, 0
    return round(statistics.median(hours), 2), len(hours)


def quality_rate(history: SupplierHistory) -> tuple[float | None, int]:
    if not history.lines:
        return None, 0
    return round(min(history.discrepancy_lines / len(history.lines), 1.0), 4), len(history.lines)


def price_cv(history: SupplierHistory) -> tuple[float | None, int]:
    cvs = []
    for series in history.prices:
        prices = [p for p in series.prices if p > 0]
        if len(prices) < 2:
            continue
        mean = statistics.fmean(prices)
        cvs.append(statistics.pstdev(prices) / mean if mean else 0.0)
    if not cvs:
        return None, 0
    return round(statistics.fmean(cvs), 4), len(cvs)


def components_of(history: SupplierHistory) -> Components:
    c = Components()
    c.otif, n = otif(history)
    c.samples["lines"] = n
    mean, sigma, n = lead_time_days(history)
    c.samples["lead_time"] = n
    if mean is not None and sigma is not None:
        c.lead_time = 1.0 - min(sigma / mean, 1.0) if mean > EPSILON else 1.0
    drift, n = promise_drift_days(history)
    c.samples["orders"] = n
    if drift is not None:
        c.promise = 1.0 if drift <= 0 else 1.0 - min(drift / 14.0, 1.0)  # two weeks late = 0
    hours, n = response_hours_median(history)
    c.samples["replies"] = n
    if hours is not None:
        c.response = 1.0 - min(hours / 48.0, 1.0)  # two days without an answer = 0
    rate, n = quality_rate(history)
    if rate is not None:
        c.quality = 1.0 - min(rate * 5.0, 1.0)  # one line in five with a problem = 0
    cv, n = price_cv(history)
    c.samples["priced_products"] = n
    if cv is not None:
        c.price = 1.0 - min(cv * 4.0, 1.0)  # 25% swing = 0
    return c


def score_from(components: Components, weights: Weights) -> float:
    weighted = 0.0
    total = 0.0
    for name, weight in weights.as_dict().items():
        value = getattr(components, name)
        if value is None or weight <= 0:
            continue
        weighted += weight * value
        total += weight
    return round(100.0 * weighted / total, 2) if total > 0 else 0.0


def trends_against(current: SupplierScore, previous: SupplierScore | None) -> list[str]:
    """Plain flags a person would want to hear about, from one run to the next."""
    if previous is None:
        return []
    flags: list[str] = []
    if (
        current.lead_time_mean_days is not None
        and previous.lead_time_mean_days
        and current.lead_time_mean_days >= previous.lead_time_mean_days * 1.3
    ):
        rise = 100 * (current.lead_time_mean_days / previous.lead_time_mean_days - 1)
        flags.append(
            f"lead time up {rise:.0f}% "
            f"({previous.lead_time_mean_days:g} to {current.lead_time_mean_days:g} days)"
        )
    if (
        current.otif is not None
        and previous.otif is not None
        and current.otif <= previous.otif - 0.10
    ):
        flags.append(f"OTIF down from {100 * previous.otif:.0f}% to {100 * current.otif:.0f}%")
    if (
        current.response_hours_median is not None
        and previous.response_hours_median
        and current.response_hours_median >= previous.response_hours_median * 1.5
    ):
        flags.append(
            f"replies slower: {current.response_hours_median:g} h median "
            f"(was {previous.response_hours_median:g} h)"
        )
    if (
        current.quality_rate is not None
        and previous.quality_rate is not None
        and current.quality_rate >= previous.quality_rate + 0.05
    ):
        flags.append(
            f"more receipt problems: {100 * current.quality_rate:.0f}% of lines "
            f"(was {100 * previous.quality_rate:.0f}%)"
        )
    return flags


def score_supplier(
    history: SupplierHistory,
    *,
    weights: Weights | None = None,
    previous: SupplierScore | None = None,
) -> SupplierScore:
    weights = weights or Weights()
    c = components_of(history)
    mean, sigma, _ = lead_time_days(history)
    drift, _ = promise_drift_days(history)
    hours, _ = response_hours_median(history)
    rate, _ = quality_rate(history)
    cv, _ = price_cv(history)
    score = SupplierScore(
        partner_id=history.partner_id,
        partner_name=history.partner_name,
        period_start=history.period_start,
        period_end=history.period_end,
        otif=c.otif,
        lead_time_mean_days=mean,
        lead_time_sigma_days=sigma,
        promise_drift_days=drift,
        response_hours_median=hours,
        quality_rate=rate,
        price_cv=cv,
        score=score_from(c, weights),
        samples=c.samples,
    )
    return score.model_copy(update={"trends": trends_against(score, previous)})
