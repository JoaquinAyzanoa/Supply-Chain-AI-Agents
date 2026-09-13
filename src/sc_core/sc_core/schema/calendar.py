"""The demand calendar: what people know about demand that the history does not.

A promotion multiplies demand while it runs, a holiday cuts it, a project
adds a known quantity over a period. Events narrow to one product or one
category, or apply to everything. The planner divides past demand by the
factor of past events (so a promotion week does not become the new normal)
and raises the forecast for events ahead.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from sc_core.schema.base import StrictModel

EventKind = Literal["promotion", "holiday", "project"]


class CalendarEvent(StrictModel):
    id: int | None = None
    kind: EventKind
    name: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date
    product_id: int | None = Field(default=None, description="one product; empty = see category")
    category: str | None = Field(default=None, description="a category path; empty = everything")
    factor: float = Field(default=1.0, gt=0, le=10, description="demand multiplier while it runs")
    quantity: float = Field(default=0.0, ge=0, description="project: units over the period")
    note: str = Field(default="", max_length=500)
    created_by: str | None = None
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _dates(self) -> CalendarEvent:
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if self.kind == "project" and self.quantity <= 0:
            raise ValueError("a project needs a quantity")
        if self.kind == "project" and self.product_id is None:
            raise ValueError("a project names the product it needs")
        return self

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def applies_to(self, product_id: int, category: str | None) -> bool:
        if self.product_id is not None:
            return self.product_id == product_id
        if self.category:
            return bool(category) and str(category).lower().startswith(self.category.lower())
        return True

    def overlap_days(self, start: date, end: date) -> int:
        """Days of the event inside [start, end]."""
        lo, hi = max(start, self.start_date), min(end, self.end_date)
        return max(0, (hi - lo).days + 1)


__all__ = ["CalendarEvent", "EventKind"]
