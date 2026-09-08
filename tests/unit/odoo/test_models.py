"""Tests for sc_core.odoo.models normalisation."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from sc_core.odoo.models import (
    Approval,
    NewOrderLine,
    Orderpoint,
    Partner,
    Picking,
    PurchaseOrder,
    PurchaseOrderLine,
    Ref,
    SupplierInfo,
    to_odoo_datetime,
)

from . import samples


def test_purchase_order_from_real_row() -> None:
    po = PurchaseOrder.from_odoo(samples.PO_ROW)
    assert po.id == 15 and po.name == "P00015"
    assert po.partner_id == Ref(id=12, name="Ready Mat")
    assert po.origin is None and po.partner_ref is None  # False -> None
    assert po.sc_eta_source is None and po.sc_pending_approval_id is None
    assert po.sc_needs_human is False  # bools stay bools
    assert po.date_planned == datetime(2026, 9, 8, 20, 21, 42, tzinfo=UTC)  # naive UTC -> aware
    assert po.order_line == [26, 27]
    assert po.is_open is True and po.is_rfq is False


def test_unknown_keys_are_ignored() -> None:
    po = PurchaseOrder.from_odoo(samples.PO_ROW)
    assert not hasattr(po, "write_date")


def test_odoo_fields_lists_declared_fields_without_id() -> None:
    fields = PurchaseOrder.odoo_fields()
    assert "id" not in fields
    assert {"name", "state", "partner_id", "sc_external_ref"} <= set(fields)


def test_line_pending_quantity() -> None:
    line = PurchaseOrderLine.from_odoo(samples.LINE_ROW)
    assert line.qty_pending == 15.0
    assert line.product_uom == Ref(id=1, name="Units")
    assert line.date_planned == datetime(2026, 10, 20, 12, tzinfo=UTC)


def test_supplierinfo_dates_and_empty_variant() -> None:
    si = SupplierInfo.from_odoo(samples.SUPPLIERINFO_ROW)
    assert si.product_id is None and si.date_start is None
    assert si.delay == 2 and si.min_qty == 12.0


def test_orderpoint_date_field() -> None:
    op = Orderpoint.from_odoo(samples.ORDERPOINT_ROW)
    assert op.lead_days_date == date(2026, 9, 13)
    assert op.trigger == "auto" and op.supplier_id is None


def test_picking_and_partner() -> None:
    pk = Picking.from_odoo(samples.PICKING_ROW)
    assert pk.purchase_id == Ref(id=15, name="P00015") and pk.date_done is None
    partner = Partner.from_odoo(samples.PARTNER_ROW)
    assert partner.email_domain == "example.com"
    assert partner.commercial_partner_id == Ref(id=15, name="Azure Interior")


def test_approval_row() -> None:
    a = Approval.from_odoo(samples.APPROVAL_ROW)
    assert a.is_pending is False
    assert a.resolved_by_id == Ref(id=2, name="Mitchell Admin")
    assert a.reason is None and a.callback_status == "sent"


def test_invalid_selection_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PurchaseOrder.from_odoo({**samples.PO_ROW, "state": "weird"})


def test_new_order_line_serialisation() -> None:
    line = NewOrderLine(
        product_id=32,
        product_qty=3,
        price_unit=10.5,
        date_planned=datetime(2026, 10, 1, 12, tzinfo=UTC),
    )
    assert line.to_odoo() == {
        "product_id": 32,
        "product_qty": 3.0,
        "price_unit": 10.5,
        "date_planned": "2026-10-01 12:00:00",
    }
    assert NewOrderLine(product_id=1, product_qty=1).to_odoo() == {
        "product_id": 1,
        "product_qty": 1.0,
    }


def test_to_odoo_datetime_converts_to_naive_utc() -> None:
    from sc_core.shared.time import LIMA

    lima = datetime(2026, 10, 1, 7, 0, tzinfo=LIMA)
    assert to_odoo_datetime(lima) == "2026-10-01 12:00:00"
