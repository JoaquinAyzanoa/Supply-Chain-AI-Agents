"""A supplier profile: how to write to this supplier, and the facts the agents keep.

People edit the wording side (formality, greeting, sign-off, contacts,
notes); the agents keep facts (reply time, last reply). The drafting prompt
gets ``profile_lines`` so every email to a supplier reads the way the buyers
want it to.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from sc_core.schema.base import StrictModel

Formality = Literal["formal", "neutral", "informal"]


class SupplierProfile(StrictModel):
    partner_id: int
    language: str | None = None
    formality: Formality | None = None
    greeting: str | None = Field(default=None, max_length=120)
    sign_off: str | None = Field(default=None, max_length=120)
    contacts: list[str] = []
    notes: str = Field(default="", max_length=2000)
    facts: dict[str, Any] = {}
    updated_at: datetime | None = None
    updated_by: str | None = None

    def is_empty(self) -> bool:
        return not any([self.formality, self.greeting, self.sign_off, self.contacts, self.notes])


def profile_lines(profile: SupplierProfile | None) -> list[str]:
    """What the drafting prompt says about the supplier; nothing when nothing is known."""
    if profile is None or profile.is_empty():
        return []
    out = ["Supplier profile (follow it):"]
    if profile.formality:
        out.append(f"- Tone: {profile.formality}")
    if profile.greeting:
        out.append(f"- Greeting to use: {profile.greeting}")
    if profile.sign_off:
        out.append(f"- Sign-off to use: {profile.sign_off}")
    if profile.contacts:
        out.append(f"- Contacts: {', '.join(profile.contacts)}")
    if profile.notes:
        out.append(f"- Notes from the buyers: {profile.notes}")
    return out
