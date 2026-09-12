"""Generate the history as a plan of dated events, before touching Odoo.

Pure and deterministic (seeded RNG): the same dataset and seed give the same
plan on any machine. The plan is simulated once to size purchases so that
on-hand stock never goes negative in the generated history.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from odoo_seed_lib.dataset import Dataset, Product


@dataclass(frozen=True)
class Delivery:
    """A customer order delivered in a week (one product, one customer)."""

    index: int
    day: date
    customer: str
    code: str
    qty: int
    backorder: bool  # delivered partially, the rest stays open


@dataclass(frozen=True)
class Purchase:
    """A supplier order and how its receipt went."""

    index: int
    supplier: str
    ordered: date
    planned: date
    lines: tuple[tuple[str, int], ...]  # (code, qty)
    outcome: str  # on_time | late | partial
    received: date  # first receipt
    received_rest: date | None  # second receipt when partial


@dataclass
class Plan:
    start: date
    end: date
    opening: dict[str, int] = field(default_factory=dict)
    deliveries: list[Delivery] = field(default_factory=list)
    purchases: list[Purchase] = field(default_factory=list)

    def demand_by_month(self, code: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.deliveries:
            if d.code == code:
                key = d.day.strftime("%Y-%m")
                out[key] = out.get(key, 0) + d.qty
        return out


def week_starts(start: date, end: date) -> list[date]:
    first = start - timedelta(days=start.weekday())  # Monday
    weeks = []
    day = first
    while day <= end:
        weeks.append(day)
        day += timedelta(days=7)
    return weeks


def weekly_demand(
    product: Product, seasonality: list[float], weeks: list[date], rng: random.Random
) -> list[int]:
    """One quantity per week from the profile: seasonality x trend x noise."""
    profile = product.demand
    span_days = max((weeks[-1] - weeks[0]).days, 1)
    out = []
    for week in weeks:
        years = (week - weeks[0]).days / 365.25
        trend = (1 + profile.trend_per_year) ** years
        mean = profile.mean_weekly * seasonality[week.month - 1] * trend
        qty = rng.gauss(mean, mean * profile.cv)
        out.append(max(0, round(qty)))
    assert span_days > 0
    return out


def allocate(
    qty: int, weights: list[tuple[str, float]], rng: random.Random
) -> list[tuple[str, int]]:
    """Split ``qty`` units across customers by weight; drops zero shares."""
    if qty <= 0:
        return []
    names = [w[0] for w in weights]
    probs = [w[1] for w in weights]
    counts = dict.fromkeys(names, 0)
    for _ in range(qty):
        counts[rng.choices(names, probs)[0]] += 1
    return [(n, c) for n, c in counts.items() if c > 0]


def build_plan(ds: Dataset, *, today: date | None = None) -> Plan:
    rng = random.Random(ds.seed)
    end = today or date.today()
    start = end - timedelta(days=int(ds.history.months * 30.44))
    weeks = [w for w in week_starts(start, end) if w <= end - timedelta(days=3)]
    plan = Plan(start=start, end=end)
    weights = [(c.name, c.weight) for c in ds.customers]

    # --- demand -------------------------------------------------------------------
    series: dict[str, list[int]] = {}
    index = 0
    for product in ds.products:
        seasonality = ds.seasonality_profiles[product.demand.seasonality]
        series[product.code] = weekly_demand(product, seasonality, weeks, rng)
        for week, qty in zip(weeks, series[product.code], strict=True):
            day = week + timedelta(days=rng.randint(0, 4))
            for customer, part in allocate(qty, weights, rng):
                index += 1
                plan.deliveries.append(
                    Delivery(
                        index=index,
                        day=day,
                        customer=customer,
                        code=product.code,
                        qty=part,
                        backorder=rng.random() < ds.history.backorder_share and part > 1,
                    )
                )
        plan.opening[product.code] = max(
            1, math.ceil(product.demand.mean_weekly * product.stock.weeks_on_hand)
        )

    # --- supply: one order per supplier per month, sized from the next month's demand ---
    receipts = ds.history.receipts
    months = _month_starts(start, end)
    pindex = 0
    for i, month in enumerate(months):
        next_month = months[i + 1] if i + 1 < len(months) else end + timedelta(days=30)
        # Next month's demand plus 5 % is what gets bought in total; every third
        # month half of it goes to the alternate supplier (when the product has
        # one) and the primary gets the rest, so stock does not pile up.
        for key in ds.suppliers:
            lines: list[tuple[str, int]] = []
            for product in ds.products:
                terms = product.suppliers.get(key)
                if terms is None:
                    continue
                need = _demand_between(
                    plan, product.code, next_month, next_month + timedelta(days=31)
                )
                total = math.ceil(need * 1.05)
                alternate_turn = (
                    "alternate" in product.suppliers and (i + _stable_hash(product.code)) % 3 == 0
                )
                alternate_qty = math.ceil(total * 0.5) if alternate_turn else 0
                if key == "primary":
                    need = total - alternate_qty
                elif alternate_turn:
                    need = alternate_qty
                else:
                    continue
                if need <= 0:
                    continue
                moq = max(terms.min_qty, 1)
                lines.append((product.code, int(math.ceil(need / moq) * moq)))
            if not lines:
                continue
            pindex += 1
            delay = max(product_delay(ds, key, lines), 1)
            ordered = month - timedelta(days=delay) - timedelta(days=rng.randint(0, 5))
            planned = ordered + timedelta(days=delay)
            roll = rng.random()
            if roll < receipts.on_time_share:
                outcome, received, rest = (
                    "on_time",
                    planned - timedelta(days=rng.randint(0, 2)),
                    None,
                )
            elif roll < receipts.on_time_share + receipts.late_share:
                late = rng.randint(1, receipts.late_days_max)
                outcome, received, rest = "late", planned + timedelta(days=late), None
            else:
                outcome, received, rest = "partial", planned, planned + timedelta(days=7)
            if received > end:
                continue  # would be open supply; handled by the dataset's open_supply list
            plan.purchases.append(
                Purchase(
                    index=pindex,
                    supplier=key,
                    ordered=ordered,
                    planned=planned,
                    lines=tuple(lines),
                    outcome=outcome,
                    received=received,
                    received_rest=rest if rest is None or rest <= end else None,
                )
            )
    _cover_shortfalls(ds, plan)
    return plan


def _stable_hash(text: str) -> int:
    """Deterministic across processes (``hash`` of str is salted per process)."""
    return sum(ord(c) * (i + 1) for i, c in enumerate(text))


def product_delay(
    ds: Dataset, supplier: str, lines: tuple[tuple[str, int], ...] | list[tuple[str, int]]
) -> int:
    delays = [ds.product(code).suppliers[supplier].delay for code, _ in lines]
    return max(delays) if delays else 30


def _month_starts(start: date, end: date) -> list[date]:
    month = date(start.year, start.month, 1)
    out = []
    while month <= end:
        out.append(month)
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return out


def _demand_between(plan: Plan, code: str, since: date, until: date) -> int:
    return sum(d.qty for d in plan.deliveries if d.code == code and since <= d.day < until)


def simulate_on_hand(plan: Plan, code: str) -> list[tuple[date, int]]:
    """Stock level after each event for ``code``, in date order (for validation)."""
    events: list[tuple[date, int, int]] = []  # (day, priority, delta): receipts before deliveries
    for p in plan.purchases:
        for line_code, qty in p.lines:
            if line_code != code:
                continue
            if p.outcome == "partial":
                half = qty // 2
                events.append((p.received, 0, half))
                if p.received_rest is not None:
                    events.append((p.received_rest, 0, qty - half))
            else:
                events.append((p.received, 0, qty))
    for d in plan.deliveries:
        if d.code == code:
            shipped = d.qty - (d.qty // 2 if d.backorder else 0)
            events.append((d.day, 1, -shipped))
    level = plan.opening.get(code, 0)
    out = []
    for day, _prio, delta in sorted(events):
        level += delta
        out.append((day, level))
    return out


def _cover_shortfalls(ds: Dataset, plan: Plan) -> None:
    """Raise the opening stock where the simulated level would dip below zero."""
    for product in ds.products:
        levels = simulate_on_hand(plan, product.code)
        lowest = min((level for _, level in levels), default=0)
        if lowest < 0:
            plan.opening[product.code] += -lowest + math.ceil(product.demand.mean_weekly)
