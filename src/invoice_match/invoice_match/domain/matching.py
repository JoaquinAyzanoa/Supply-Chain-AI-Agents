"""Deterministic matching: which order, which lines, what differs.

No model here. Lines map by product reference first (the ``[CODE]`` Odoo
puts in product names), then by description similarity (stdlib
``difflib``); quantities compare against what was received and not yet
billed; prices against the order line within a tolerance.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from invoice_match.domain.models import PoCandidate
from sc_core.schema.a2a import BillMatch, InvoiceData, InvoiceLine, MatchLine, MatchStatus
from supplier_comms.domain.models import LineView, PoContext

PO_NAME = re.compile(r"\bP\d{5}\b")
REF_IN_NAME = re.compile(r"\[([^\]]+)\]")
EPSILON = 1e-6


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def product_ref_of(line: LineView) -> str | None:
    """``[CBEA-LHN] Válvula…`` -> ``CBEA-LHN``."""
    found = REF_IN_NAME.search(line.product)
    return found.group(1).strip().upper() if found else None


def find_po_reference(invoice: InvoiceData, *texts: str) -> str | None:
    """The order the invoice names, from its reference field or anywhere in the texts."""
    for candidate in (invoice.po_reference or "", *texts):
        found = PO_NAME.search(candidate)
        if found:
            return found.group(0)
    return None


def pick_by_amount(
    invoice: InvoiceData, candidates: list[PoCandidate], *, tolerance_pct: float
) -> tuple[PoCandidate | None, list[str]]:
    """The one order whose total matches the invoice; ``None`` with the reason otherwise."""
    if not candidates:
        return None, ["the supplier has no open order to match"]
    amount = invoice.total if invoice.total is not None else invoice.subtotal
    if amount is None:
        return None, ["the invoice shows no total to match an order by"]
    close = [
        c
        for c in candidates
        if abs(c.amount_total - amount) <= max(abs(amount) * tolerance_pct / 100.0, 0.01)
        or abs(c.amount_untaxed - amount) <= max(abs(amount) * tolerance_pct / 100.0, 0.01)
    ]
    if len(close) == 1:
        return close[0], []
    if len(close) > 1:
        names = ", ".join(c.name for c in close)
        return None, [f"{len(close)} orders of this supplier have the same total ({names})"]
    if len(candidates) == 1:
        return None, [
            f"the invoice total {amount:.2f} does not match the supplier's only open order "
            f"{candidates[0].name} ({candidates[0].amount_total:.2f})"
        ]
    return None, ["no order of this supplier has the invoice's total"]


# What the person deciding on the bill reads, in the instance's language.
WORDS: dict[str, dict[str, str]] = {
    "en": {
        "unmatched": "no order line looks like this one",
        "price": "billed {billed:.2f}, ordered {ordered:.2f} ({diff:+.2f})",
        "not_received": "billed {billed:g}, received and not yet billed {billable:g}",
        "nothing_received": "billed {billed:g}, nothing received yet",
        "qty": "billed {billed:g}, ordered {ordered:g}",
        "partial": "partial: {billed:g} of {billable:g} billable",
        "count": "{n} line(s) {what}",
        "currency": "invoice in {invoice}, order in {order}",
        "no_lines": "no lines could be read from the invoice",
    },
    "es": {
        "unmatched": "ninguna línea de la orden se parece a esta",
        "price": "facturado {billed:.2f}, ordenado {ordered:.2f} ({diff:+.2f})",
        "not_received": "facturado {billed:g}, recibido y aún sin facturar {billable:g}",
        "nothing_received": "facturado {billed:g}, aún no se recibió nada",
        "qty": "facturado {billed:g}, ordenado {ordered:g}",
        "partial": "parcial: {billed:g} de {billable:g} por facturar",
        "count": "{n} línea(s) con {what}",
        "status.price_variance": "diferencia de precio",
        "status.qty_variance": "diferencia de cantidad",
        "status.not_received": "mercadería no recibida",
        "status.unmatched": "sin línea de orden",
        "currency": "factura en {invoice}, orden en {order}",
        "no_lines": "no se pudo leer ninguna línea de la factura",
    },
}


def match_lines(
    invoice: InvoiceData,
    ctx: PoContext,
    *,
    price_tolerance_pct: float,
    qty_tolerance_pct: float,
    fuzzy_threshold: float,
    language: str = "en",
) -> list[MatchLine]:
    words = WORDS.get(language, WORDS["en"])
    unused = list(ctx.lines)
    out: list[MatchLine] = []
    for inv in invoice.lines:
        line, score = _best_line(inv, unused, fuzzy_threshold)
        if line is None:
            out.append(
                MatchLine(
                    po_line_id=None,
                    product=None,
                    invoice_description=inv.description,
                    invoice_qty=inv.qty,
                    invoice_price=inv.unit_price,
                    status="unmatched",
                    note=words["unmatched"],
                    similarity=score,
                )
            )
            continue
        unused.remove(line)
        status, note = _compare(inv, line, price_tolerance_pct, qty_tolerance_pct, words)
        out.append(
            MatchLine(
                po_line_id=line.id,
                product=line.product,
                invoice_description=inv.description,
                invoice_qty=inv.qty,
                invoice_price=inv.unit_price,
                po_qty=line.qty,
                po_price=line.price_unit,
                received_qty=line.qty_received,
                invoiced_qty=line.qty_invoiced,
                status=status,
                note=note,
                similarity=score,
            )
        )
    return out


def _best_line(
    inv: InvoiceLine, lines: list[LineView], threshold: float
) -> tuple[LineView | None, float]:
    wanted = (inv.product_ref or "").strip().upper()
    text = f"{inv.product_ref or ''} {inv.description}"
    best: LineView | None = None
    best_score = 0.0
    for line in lines:
        ref = product_ref_of(line)
        if ref and (ref == wanted or ref in text.upper()):
            return line, 1.0
        score = similarity(text, line.product)
        if score > best_score:
            best, best_score = line, score
    if best is not None and best_score >= threshold:
        return best, best_score
    return None, best_score


def _compare(
    inv: InvoiceLine, line: LineView, price_tol: float, qty_tol: float, words: dict[str, str]
) -> tuple[MatchStatus, str | None]:
    if inv.unit_price is not None:
        allowed = abs(line.price_unit) * price_tol / 100.0
        diff = inv.unit_price - line.price_unit
        if abs(diff) > allowed + EPSILON:
            return "price_variance", words["price"].format(
                billed=inv.unit_price, ordered=line.price_unit, diff=diff
            )
    if inv.qty is not None:
        billable = max(line.qty_received - line.qty_invoiced, 0.0)
        allowed_qty = abs(billable) * qty_tol / 100.0
        if inv.qty > billable + allowed_qty + EPSILON:
            return "not_received", (
                words["not_received"].format(billed=inv.qty, billable=billable)
                if line.qty_received > 0
                else words["nothing_received"].format(billed=inv.qty)
            )
        if inv.qty > line.qty + EPSILON:
            return "qty_variance", words["qty"].format(billed=inv.qty, ordered=line.qty)
        if inv.qty + EPSILON < billable:
            return "ok", words["partial"].format(billed=inv.qty, billable=billable)
    return "ok", None


def verdict_for(lines: list[MatchLine], language: str = "en") -> tuple[str, list[str]]:
    words = WORDS.get(language, WORDS["en"])
    reasons: list[str] = []
    counts: dict[str, int] = {}
    for line in lines:
        if line.status != "ok":
            counts[line.status] = counts.get(line.status, 0) + 1
    for status, n in counts.items():
        reasons.append(
            words["count"].format(n=n, what=words.get(f"status.{status}", status.replace("_", " ")))
        )
    return ("clean" if not counts else "hold"), reasons


def build_match(
    invoice: InvoiceData,
    ctx: PoContext,
    *,
    price_tolerance_pct: float,
    qty_tolerance_pct: float,
    fuzzy_threshold: float,
    language: str = "en",
) -> BillMatch:
    words = WORDS.get(language, WORDS["en"])
    lines = match_lines(
        invoice,
        ctx,
        price_tolerance_pct=price_tolerance_pct,
        qty_tolerance_pct=qty_tolerance_pct,
        fuzzy_threshold=fuzzy_threshold,
        language=language,
    )
    verdict, reasons = verdict_for(lines, language)
    expected = sum(
        (m.invoice_qty or 0.0) * (m.po_price or 0.0) for m in lines if m.po_line_id is not None
    )
    if invoice.currency and ctx.currency and invoice.currency.upper() != ctx.currency.upper():
        verdict = "hold"
        reasons.append(words["currency"].format(invoice=invoice.currency, order=ctx.currency))
    if not invoice.lines:
        verdict = "hold"
        reasons.append(words["no_lines"])
    return BillMatch(
        po_name=ctx.name,
        verdict=verdict,  # type: ignore[arg-type]
        lines=lines,
        invoice_total=invoice.total,
        invoice_subtotal=invoice.subtotal,
        expected_subtotal=round(expected, 2),
        price_tolerance_pct=price_tolerance_pct,
        reasons=reasons,
    )
