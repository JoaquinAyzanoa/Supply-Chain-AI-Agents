"""Replenishment proposal contract (phase 7), shared by the planner, the director and the UI.

Every number on a line comes from ``inventory_planning.policy.formulas``
and the stored inputs; the model may only fill ``explanation`` and, for a
single-product review with text context, pick ``action`` from the fixed
set. The proposal travels inside ``InventoryPlanningResult`` over A2A and
is what the approval shows.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar, Literal

from pydantic import Field

from sc_core.schema.base import StrictModel

LineAction = Literal[
    "update_rule", "create_rfq", "update_rule_and_rfq", "hold", "manual_review", "none"
]
ExceptionKind = Literal[
    "stockout_risk",
    "overstock",
    "lead_time_drift",
    "no_supplier",
    "negative_position",
    "no_history",
]


class ReplenishmentLine(StrictModel):
    line_id: str = Field(min_length=1, description="<run_id>:<product_id>")
    product_id: int
    product_ref: str
    product_name: str = ""
    warehouse_id: int
    # inputs
    on_hand: float
    reserved: float = 0.0
    incoming: float
    position: float = Field(description="on hand - reserved + incoming")
    forecast_daily: float = Field(ge=0)
    forecast_method: str
    sigma_daily: float = Field(ge=0)
    wape: float | None = None
    mape: float | None = None
    history_periods: int = Field(ge=0)
    lead_time_days: float = Field(ge=0)
    sigma_lead_time_days: float = Field(ge=0)
    service_level: float
    review_period_days: int
    abc_class: str
    # outputs
    ss: float = Field(ge=0)
    rop: float = Field(ge=0)
    order_up_to: float = Field(ge=0)
    coverage_days: float | None = None
    current_min: float | None = None
    current_max: float | None = None
    proposed_min: float = Field(ge=0)
    proposed_max: float = Field(ge=0)
    order_qty: float = Field(ge=0)
    supplier_id: int | None = None
    supplier_name: str | None = None
    unit_price: float | None = None
    currency: str | None = None
    moq: float = 0.0
    # judgement
    exception: ExceptionKind | None = None
    explanation: str | None = None
    action: LineAction = "none"

    @property
    def order_value(self) -> float:
        return self.order_qty * (self.unit_price or 0.0)


class ReplenishmentProposal(StrictModel):
    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int = Field(default=1, ge=1)
    run_id: str
    as_of: date
    warehouse_id: int
    warehouse_code: str = ""
    lines: list[ReplenishmentLine] = []
    summary: str = ""
    totals: dict[str, float] = {}

    def compute_totals(self) -> dict[str, float]:
        rfq_lines = [ln for ln in self.lines if ln.action in ("create_rfq", "update_rule_and_rfq")]
        rules = [ln for ln in self.lines if ln.action in ("update_rule", "update_rule_and_rfq")]
        value: dict[str, float] = {}
        for line in rfq_lines:
            key = f"rfq_value_{line.currency or 'n/a'}"
            value[key] = value.get(key, 0.0) + line.order_value
        return {
            "lines": float(len(self.lines)),
            "rfq_lines": float(len(rfq_lines)),
            "rules_changed": float(len(rules)),
            "exceptions": float(sum(1 for ln in self.lines if ln.exception)),
            "manual_review": float(sum(1 for ln in self.lines if ln.action == "manual_review")),
            **value,
        }
