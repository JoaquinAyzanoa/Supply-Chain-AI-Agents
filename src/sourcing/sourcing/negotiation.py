"""Counter-offers within the buyer's limits, in code.

The target is what the evidence supports: what we last paid, the best
competing price on the list, or the number a buyer typed. The cap bounds how
far below the quoted price we may ask in one move, so the floor is
``current * (1 - cap)``. The offer is the target, never below the floor,
never at or above the quoted price. Rounds are counted so a supplier is not
chased forever.
"""

from __future__ import annotations

from dataclasses import dataclass

from sc_core.shared.errors import ValidationFailed


@dataclass(frozen=True)
class OfferPlan:
    current_price: float
    target_price: float
    floor_price: float
    offered_price: float
    cap_pct: float
    round_no: int
    max_rounds: int
    basis: str


BASIS: dict[str, dict[str, str]] = {
    "en": {
        "target": "the buyer's target",
        "last_paid": "our last paid price of {price:.2f}",
        "competing": "a competing price of {price:.2f}",
        "opening": "a standard opening ask",
    },
    "es": {
        "target": "el objetivo del comprador",
        "last_paid": "nuestro último precio pagado, {price:.2f}",
        "competing": "un precio de la competencia, {price:.2f}",
        "opening": "un pedido de apertura estándar",
    },
}


def floor_for(current_price: float, cap_pct: float) -> float:
    return round(current_price * (1.0 - cap_pct / 100.0), 2)


def plan_offer(
    *,
    current_price: float,
    last_paid: float | None,
    competing: list[float],
    target_override: float | None,
    cap_pct: float,
    round_no: int,
    max_rounds: int,
    language: str = "en",
) -> OfferPlan | None:
    """The move to make, or None when there is nothing to negotiate."""
    if current_price <= 0:
        raise ValidationFailed("the quoted price must be positive")
    if round_no > max_rounds:
        return None
    basis_words = BASIS.get(language, BASIS["en"])  # the buyer reads the basis in the approval
    candidates: list[tuple[float, str]] = []
    if target_override is not None:
        candidates.append((target_override, basis_words["target"]))
    if last_paid is not None and last_paid > 0:
        candidates.append((last_paid, basis_words["last_paid"].format(price=last_paid)))
    positive = [p for p in competing if p > 0]
    if positive:
        candidates.append((min(positive), basis_words["competing"].format(price=min(positive))))
    if candidates:
        target, basis = min(candidates, key=lambda item: item[0])
        if target >= current_price:
            return None  # the quote is already at or under what we know
    else:
        # no evidence: a modest opening ask, half the cap
        target, basis = round(current_price * (1.0 - cap_pct / 200.0), 2), basis_words["opening"]
    floor = floor_for(current_price, cap_pct)
    offered = round(max(target, floor), 2)
    if offered >= current_price:
        return None
    return OfferPlan(
        current_price=current_price,
        target_price=round(target, 2),
        floor_price=floor,
        offered_price=offered,
        cap_pct=cap_pct,
        round_no=round_no,
        max_rounds=max_rounds,
        basis=basis,
    )


def check_edited_offer(offered: float, *, current_price: float, floor_price: float) -> float:
    """A person may change the number, never past the limits."""
    if offered < floor_price - 1e-9:
        raise ValidationFailed(
            f"an offer of {offered:.2f} is below the floor of {floor_price:.2f} (the cap)"
        )
    if offered >= current_price:
        raise ValidationFailed(
            f"an offer of {offered:.2f} is not below the quoted price of {current_price:.2f}"
        )
    return round(offered, 2)


__all__ = ["OfferPlan", "check_edited_offer", "floor_for", "plan_offer"]
