"""Matching is arithmetic: the table says what an accounts clerk would say."""

from __future__ import annotations

from invoice_match.domain.matching import build_match, find_po_reference, pick_by_amount, similarity
from invoice_match.domain.models import PoCandidate
from sc_core.schema.a2a import InvoiceData, InvoiceLine
from supplier_comms.domain.models import LineView
from supplier_comms.testing import demo_context

from .conftest import received_context


def invoice(**over: object) -> InvoiceData:
    base = {
        "supplier_name": "Proveedor Hidraulica",
        "invoice_number": "F001-000123",
        "currency": "PEN",
        "lines": [
            InvoiceLine(description="Bomba hidraulica 2HP", qty=2, unit_price=500.0, total=1000.0),
            InvoiceLine(description='Manguera 1/2"', qty=20, unit_price=12.5, total=250.0),
        ],
        "subtotal": 1250.0,
        "total": 1475.0,
    }
    return InvoiceData(**{**base, **over})  # type: ignore[arg-type]


def match(inv: InvoiceData, ctx: object = None, **over: float | str) -> object:
    params: dict[str, float | str] = {
        "price_tolerance_pct": 1.0,
        "qty_tolerance_pct": 0.0,
        "fuzzy_threshold": 0.6,
    }
    params.update(over)
    return build_match(inv, ctx or received_context(), **params)  # type: ignore[arg-type]


def test_perfect_invoice_is_clean() -> None:
    result = match(invoice())
    assert result.verdict == "clean" and result.reasons == []  # type: ignore[attr-defined]
    assert [m.status for m in result.lines] == ["ok", "ok"]  # type: ignore[attr-defined]
    assert [m.po_line_id for m in result.lines] == [31, 32]  # type: ignore[attr-defined]
    assert result.expected_subtotal == 1250.0  # type: ignore[attr-defined]


def test_price_three_percent_up_is_a_variance_one_percent_is_not() -> None:
    up3 = invoice(lines=[InvoiceLine(description="Bomba hidraulica 2HP", qty=2, unit_price=515.0)])
    result = match(up3)
    assert result.verdict == "hold" and result.lines[0].status == "price_variance"  # type: ignore[attr-defined]
    assert "billed 515.00, ordered 500.00 (+15.00)" == result.lines[0].note  # type: ignore[attr-defined]
    assert result.reasons == ["1 line(s) price variance"]  # type: ignore[attr-defined]
    up1 = invoice(lines=[InvoiceLine(description="Bomba hidraulica 2HP", qty=2, unit_price=504.0)])
    assert match(up1).verdict == "clean"  # type: ignore[attr-defined]


def test_the_notes_follow_the_language_of_the_person_who_decides() -> None:
    up3 = invoice(lines=[InvoiceLine(description="Bomba hidraulica 2HP", qty=2, unit_price=515.0)])
    result = match(up3, language="es")
    assert result.lines[0].status == "price_variance"  # type: ignore[attr-defined]
    assert result.lines[0].note == "facturado 515.00, ordenado 500.00 (+15.00)"  # type: ignore[attr-defined]
    assert result.reasons == ["1 línea(s) con diferencia de precio"]  # type: ignore[attr-defined]


def test_billing_more_than_received_is_not_received() -> None:
    ctx = demo_context()  # nothing received yet on the demo order
    result = match(invoice(), ctx)
    assert result.verdict == "hold"  # type: ignore[attr-defined]
    assert all(m.status == "not_received" for m in result.lines)  # type: ignore[attr-defined]
    assert result.lines[0].note == "billed 2, nothing received yet"  # type: ignore[attr-defined]
    partly = received_context().model_copy(
        update={
            "lines": [
                LineView(**{**line.model_dump(), "qty_invoiced": 1.0})
                for line in received_context().lines
            ]
        }
    )
    result = match(invoice(lines=[InvoiceLine(description="Bomba hidraulica 2HP", qty=2)]), partly)
    assert result.lines[0].status == "not_received"  # type: ignore[attr-defined]
    assert result.lines[0].note == "billed 2, received and not yet billed 1"  # type: ignore[attr-defined]


def test_partial_invoice_is_fine() -> None:
    result = match(
        invoice(lines=[InvoiceLine(description="Bomba hidraulica 2HP", qty=1, unit_price=500)])
    )
    assert result.verdict == "clean" and result.lines[0].note == "partial: 1 of 2 billable"  # type: ignore[attr-defined]


def test_descriptions_map_by_code_then_by_similarity_then_give_up() -> None:
    ctx = received_context().model_copy(
        update={
            "lines": [
                LineView(**{**ctx_line.model_dump(), "product": "[BH-2HP] Bomba hidráulica 2HP"})
                if ctx_line.id == 31
                else ctx_line
                for ctx_line in received_context().lines
            ]
        }
    )
    by_code = match(
        invoice(
            lines=[InvoiceLine(description="Pump", product_ref="bh-2hp", qty=2, unit_price=500)]
        ),
        ctx,
    )
    assert by_code.lines[0].po_line_id == 31 and by_code.lines[0].similarity == 1.0  # type: ignore[attr-defined]
    fuzzy = match(
        invoice(lines=[InvoiceLine(description="MANGUERA 1/2 pulgada", qty=20, unit_price=12.5)])
    )
    assert fuzzy.lines[0].po_line_id == 32 and fuzzy.lines[0].similarity >= 0.6  # type: ignore[attr-defined]
    nothing = match(
        invoice(lines=[InvoiceLine(description="Servicio de transporte", qty=1, unit_price=80)])
    )
    assert nothing.lines[0].status == "unmatched" and nothing.verdict == "hold"  # type: ignore[attr-defined]
    assert similarity("Bomba hidráulica 2HP", "bomba hidraulica 2 hp") > 0.8


def test_currency_mismatch_and_empty_invoice_are_held() -> None:
    assert "invoice in USD, order in PEN" in match(invoice(currency="USD")).reasons  # type: ignore[attr-defined]
    empty = match(invoice(lines=[]))
    assert empty.verdict == "hold" and "no lines could be read" in empty.reasons[0]  # type: ignore[attr-defined]


def test_order_reference_is_found_in_the_invoice_or_the_email() -> None:
    assert find_po_reference(invoice(po_reference="OC P00016")) == "P00016"
    assert find_po_reference(invoice(), "Ref. su orden P00023, gracias") == "P00023"
    assert find_po_reference(invoice(), "sin referencia") is None


def test_order_by_amount_needs_exactly_one_match() -> None:
    a = PoCandidate(
        id=1, name="P00001", state="purchase", amount_total=1475.0, amount_untaxed=1250.0
    )
    b = PoCandidate(
        id=2, name="P00002", state="purchase", amount_total=1475.0, amount_untaxed=1250.0
    )
    c = PoCandidate(id=3, name="P00003", state="purchase", amount_total=99.0, amount_untaxed=90.0)
    chosen, reasons = pick_by_amount(invoice(), [a, c], tolerance_pct=1.0)
    assert chosen is a and reasons == []
    chosen, reasons = pick_by_amount(invoice(), [a, b], tolerance_pct=1.0)
    assert chosen is None and "2 orders of this supplier have the same total" in reasons[0]
    chosen, reasons = pick_by_amount(invoice(), [c], tolerance_pct=1.0)
    assert chosen is None and "does not match the supplier's only open order P00003" in reasons[0]
    assert pick_by_amount(invoice(), [], tolerance_pct=1.0) == (
        None,
        ["the supplier has no open order to match"],
    )
    assert pick_by_amount(invoice(total=None, subtotal=None), [a], tolerance_pct=1.0)[1] == [
        "the invoice shows no total to match an order by"
    ]
