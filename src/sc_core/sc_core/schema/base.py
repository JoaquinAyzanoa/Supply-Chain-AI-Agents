"""Base models for every contract in the system.

``StrictModel`` is the default for anything that crosses a boundary: events,
A2A tasks and results, Odoo record snapshots, approval payloads. Strictness
is what makes contract drift visible: an unexpected field is an error, not
silently dropped data, and instances cannot be mutated after validation so
a value observed once is the value that was logged and traced.

``MutableModel`` keeps the same validation but allows assignment; use it for
builder-style objects that are filled in steps (a proposal under
construction) and convert to a strict model before publishing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        populate_by_name=True,
    )


class MutableModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        validate_assignment=True,
        str_strip_whitespace=True,
        validate_default=True,
        populate_by_name=True,
    )
