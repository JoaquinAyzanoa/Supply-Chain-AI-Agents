"""Rows as the Odoo 18 demo database returns them (captured 2026-09-08).

Keep these verbatim: the point is to test normalisation of real shapes
(``False`` for empty, ``[id, name]`` for many2one, naive UTC datetimes).
"""

from __future__ import annotations

from typing import Any

PO_ROW: dict[str, Any] = {
    "id": 15,
    "name": "P00015",
    "state": "purchase",
    "partner_id": [12, "Ready Mat"],
    "date_order": "2026-09-10 20:21:42",
    "date_planned": "2026-09-08 20:21:42",
    "date_approve": "2026-09-08 20:21:42",
    "amount_total": 6936.0,
    "amount_untaxed": 6936.0,
    "currency_id": [1, "USD"],
    "order_line": [26, 27],
    "receipt_status": "pending",
    "picking_ids": [13],
    "origin": False,
    "user_id": [2, "Mitchell Admin"],
    "company_id": [1, "My Company (San Francisco)"],
    "sc_external_ref": False,
    "sc_eta_source": False,
    "sc_eta_confidence": 0.0,
    "sc_needs_human": False,
    "sc_pending_approval_id": False,
    "partner_ref": False,
    "write_date": "2026-09-08 20:40:00",  # not declared on the model: must be ignored
}

LINE_ROW: dict[str, Any] = {
    "id": 26,
    "order_id": [15, "P00015"],
    "name": "[FURN_6667] Acoustic Bloc Screens (White)",
    "product_id": [32, "[FURN_6667] Acoustic Bloc Screens (White)"],
    "product_qty": 20.0,
    "product_uom": [1, "Units"],
    "price_unit": 286.8,
    "price_subtotal": 5736.0,
    "date_planned": "2026-10-20 12:00:00",
    "qty_received": 5.0,
    "qty_invoiced": 0.0,
    "currency_id": [1, "USD"],
    "state": "purchase",
}

SUPPLIERINFO_ROW: dict[str, Any] = {
    "id": 18,
    "partner_id": [11, "Gemini Furniture"],
    "product_tmpl_id": [5, "[FURN_7777] Office Chair"],
    "product_id": False,
    "product_name": False,
    "product_code": False,
    "min_qty": 12.0,
    "price": 90.0,
    "currency_id": [1, "USD"],
    "delay": 2,
    "date_start": False,
    "date_end": False,
    "sequence": 1,
}

ORDERPOINT_ROW: dict[str, Any] = {
    "id": 1,
    "name": "OP/00001",
    "product_id": [6, "[FURN_8888] Office Lamp"],
    "warehouse_id": [1, "YourCompany"],
    "location_id": [8, "WH/Stock"],
    "product_min_qty": 5.0,
    "product_max_qty": 10.0,
    "qty_to_order": 0.0,
    "qty_forecast": 50.0,
    "qty_on_hand": 0.0,
    "trigger": "auto",
    "lead_days_date": "2026-09-13",
    "supplier_id": False,
    "active": True,
}

PICKING_ROW: dict[str, Any] = {
    "id": 13,
    "name": "WH/IN/00006",
    "state": "assigned",
    "partner_id": [12, "Ready Mat"],
    "scheduled_date": "2026-09-08 20:21:42",
    "date_deadline": "2026-09-08 20:21:42",
    "date_done": False,
    "origin": "P00015",
    "purchase_id": [15, "P00015"],
    "picking_type_code": "incoming",
    "move_ids": [30, 31],
    "sc_eta_source": False,
    "sc_needs_human": False,
    "sc_last_run_id": False,
}

PARTNER_ROW: dict[str, Any] = {
    "id": 15,
    "name": "Azure Interior",
    "email": "azure.Interior24@example.com",
    "email_normalized": "azure.interior24@example.com",
    "is_company": True,
    "supplier_rank": 1,
    "parent_id": False,
    "commercial_partner_id": [15, "Azure Interior"],
    "child_ids": [27, 34, 28],
    "lang": "en_US",
    "active": True,
}

APPROVAL_ROW: dict[str, Any] = {
    "id": 2,
    "kind": "po_change",
    "summary": "Move ETA to 2026-10-20 (test)",
    "status": "approved",
    "res_model": "purchase.order",
    "res_id": 15,
    "po_id": [15, "P00015"],
    "payload_json": '{"changes": [{"line_id": 26, "field": "date_planned"}]}',
    "requested_by": "supplier_comms",
    "case_id": "case_test_1",
    "run_id": "run_test_1",
    "thread_id": "case_test_1",
    "resolved_by_id": [2, "Mitchell Admin"],
    "resolved_at": "2026-09-08 20:41:10",
    "reason": False,
    "callback_status": "sent",
    "callback_error": False,
    "create_date": "2026-09-08 20:41:05",
}
