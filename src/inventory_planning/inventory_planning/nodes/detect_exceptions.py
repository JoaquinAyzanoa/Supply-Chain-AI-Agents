"""Rules that flag a line and decide its action. No model involved.

Exceptions, first match wins:

* ``no_supplier``       nobody to buy from → ``manual_review``
* ``no_history``        too few periods and nothing on hand to reason about → ``manual_review``
* ``negative_position`` backorders exceed stock and incoming → order (``create_rfq``)
* ``stockout_risk``     the position does not cover the demand over the lead time → order
* ``overstock``         coverage beyond the class's maximum → fix the rule, never order
* ``lead_time_drift``   the measured lead time differs from the promise by more than a quarter

The action is then derived from what changes: an order (``order_qty > 0``),
a rule change (no rule yet, or min/max moved by more than the tolerance),
both, or nothing.
"""

from __future__ import annotations

from inventory_planning.policy import ProductParams
from sc_core.schema.planning import ExceptionKind, LineAction, ReplenishmentLine

MIN_HISTORY_PERIODS = 8
RULE_TOLERANCE = 0.10  # relative change below which the current rule is left alone
LEAD_TIME_DRIFT = 0.25


def detect(
    line: ReplenishmentLine, params: ProductParams, *, promised_lead_time: float | None = None
) -> ReplenishmentLine:
    exception = _exception(line, params, promised_lead_time)
    action = _action(line, exception)
    return line.model_copy(update={"exception": exception, "action": action})


def detect_all(
    lines: list[ReplenishmentLine],
    params: dict[int, ProductParams],
    promised_lead_times: dict[int, float] | None = None,
) -> list[ReplenishmentLine]:
    promised = promised_lead_times or {}
    return [
        detect(line, params[line.product_id], promised_lead_time=promised.get(line.product_id))
        for line in lines
    ]


def rule_changes(line: ReplenishmentLine) -> bool:
    """A rule is worth writing when there is none or min/max move beyond the tolerance."""
    if line.current_min is None or line.current_max is None:
        return line.proposed_max > 0
    return _moved(line.current_min, line.proposed_min) or _moved(
        line.current_max, line.proposed_max
    )


def _exception(
    line: ReplenishmentLine, params: ProductParams, promised: float | None
) -> ExceptionKind | None:
    if line.supplier_id is None:
        return "no_supplier"
    if line.history_periods < MIN_HISTORY_PERIODS and line.forecast_daily == 0:
        return "no_history"
    if line.position < 0:
        return "negative_position"
    if line.forecast_daily > 0 and line.position < line.forecast_daily * line.lead_time_days:
        return "stockout_risk"
    if line.coverage_days is not None and line.coverage_days > params.max_coverage_days:
        return "overstock"
    if (
        promised
        and params.lead_time_mean_days is not None
        and abs(params.lead_time_mean_days - promised) / promised > LEAD_TIME_DRIFT
    ):
        return "lead_time_drift"
    return None


def _action(line: ReplenishmentLine, exception: ExceptionKind | None) -> LineAction:
    if exception in ("no_supplier", "no_history"):
        return "manual_review"
    rule = rule_changes(line)
    order = line.order_qty > 0 and exception != "overstock"
    if order and rule:
        return "update_rule_and_rfq"
    if order:
        return "create_rfq"
    if rule:
        return "update_rule"
    return "none"


def _moved(current: float, proposed: float) -> bool:
    return abs(proposed - current) > max(1.0, RULE_TOLERANCE * abs(current))
