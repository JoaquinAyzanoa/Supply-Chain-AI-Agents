"""Receipt reconciliation is arithmetic: the table says what a clerk would say."""

from __future__ import annotations

import pytest

from logistics.reconcile import reconcile
from logistics.testing import demo_receipt


def test_exact_receipt_matches() -> None:
    result = reconcile(demo_receipt())
    assert result.ok and result.lines == 2 and result.discrepancies == []
    assert result.picking_name == "WH/IN/00042" and result.tolerance_pct == 0.0


def test_short_line_is_a_discrepancy_at_zero_tolerance() -> None:
    result = reconcile(demo_receipt(short_by=2))
    [short] = result.discrepancies
    assert short.kind == "short" and short.po_line_id == 31 and short.product.startswith("Bomba")
    assert (short.expected, short.received, short.difference) == (2.0, 0.0, -2.0)


def test_small_shortfall_within_tolerance_is_fine() -> None:
    receipt = demo_receipt(short_by=0.3)  # 15% of the pump line
    assert reconcile(receipt, tolerance_pct=20).ok
    assert not reconcile(receipt, tolerance_pct=10).ok


def test_extra_line_not_on_the_order_is_over() -> None:
    result = reconcile(demo_receipt(extra_line=True))
    [over] = result.discrepancies
    assert over.kind == "over" and over.po_line_id is None and over.product == "Filtro de retorno"
    assert over.expected == 0.0 and over.received == 1.0 and over.uom == "Unidades"


def test_negative_tolerance_is_refused() -> None:
    with pytest.raises(ValueError, match="tolerance"):
        reconcile(demo_receipt(), tolerance_pct=-1)
