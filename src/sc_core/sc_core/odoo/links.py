"""Deep links into the Odoo web client (Odoo 17+ URL scheme)."""

from __future__ import annotations


def record_url(base_url: str, model: str, record_id: int) -> str:
    """``http://odoo:8069/odoo/purchase.order/15`` opens the record's form."""
    return f"{base_url.rstrip('/')}/odoo/{model}/{record_id}"
