"""The purchase-order token carried in subjects: ``[P00015]``.

Suppliers usually keep the subject when they reply, so the token is the
cheapest reliable link between a message and an order. Odoo names orders
``P00015`` (RFQs too), requisitions ``PR00001``; the pattern accepts any
short upper-case prefix followed by digits.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\[([A-Z]{1,5}\d{3,})\]")

HEADER_PO = "x-sc-po"
HEADER_CASE = "x-sc-case"


def make(po_name: str) -> str:
    name = po_name.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,5}\d{3,}", name):
        raise ValueError(f"not an order name: {po_name!r}")
    return f"[{name}]"


def parse(subject: str | None) -> str | None:
    """First token in ``subject`` (``Re: [P00015] ...`` -> ``P00015``), or ``None``."""
    match = _TOKEN.search(subject or "")
    return match.group(1) if match else None


def tag_subject(subject: str, po_name: str) -> str:
    """Prefix the token unless the subject already carries it."""
    token = make(po_name)
    if parse(subject) == po_name.strip().upper():
        return subject
    return f"{token} {subject.strip()}".strip()


def headers_for(po_name: str, case_id: str | None = None) -> dict[str, str]:
    """Custom internet headers that survive subject edits by the supplier."""
    headers = {HEADER_PO: po_name.strip().upper()}
    if case_id:
        headers[HEADER_CASE] = case_id
    return headers
