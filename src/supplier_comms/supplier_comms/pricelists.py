"""Price lists in spreadsheets: find the header, read the rows, diff against Odoo.

No model here. A table is a price list when one of its first rows names a
code column and a price column (English or Spanish headers). Each row with
a code and a price becomes a proposed ``product.supplierinfo`` update once
the code is matched to a product we buy: by our own product code, or by the
code the supplier already uses on our price list. Rows we cannot match are
counted and shown, never written.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sc_core.mail.tables import TableData
from sc_core.schema.a2a import PriceListDiff, PriceListRow

HEADER_ROWS_TO_SCAN = 15
MAX_ROWS = 500

# Header words per column, lower-case and accent-free. A cell matches when it equals one
# of them or starts with one followed by a space or a symbol ("precio usd", "code #").
HEADERS: dict[str, tuple[str, ...]] = {
    "code": (
        "code",
        "codigo",
        "cod",
        "ref",
        "reference",
        "referencia",
        "sku",
        "part number",
        "part no",
        "part",
        "item",
        "articulo",
        "modelo",
        "model",
    ),
    "description": (
        "description",
        "descripcion",
        "product",
        "producto",
        "name",
        "nombre",
        "detail",
        "detalle",
    ),
    "price": (
        "price",
        "precio",
        "unit price",
        "precio unitario",
        "p unit",
        "p.unit",
        "pu",
        "cost",
        "costo",
        "valor",
        "usd",
        "pen",
        "eur",
    ),
    "currency": ("currency", "moneda", "cur", "divisa"),
    "min_qty": (
        "min qty",
        "min. qty",
        "moq",
        "cantidad minima",
        "cant minima",
        "cant. minima",
        "minimo",
        "min",
        "qty min",
    ),
    "lead_days": (
        "lead time",
        "lead days",
        "lead",
        "delivery days",
        "delivery",
        "plazo",
        "entrega",
        "dias",
        "days",
    ),
}
CURRENCIES = ("USD", "PEN", "EUR", "GBP", "CLP", "COP", "MXN", "BRL", "ARS")
CODE_IN_NAME = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class ParsedRow:
    code: str
    description: str
    price: float
    currency: str | None
    min_qty: float
    lead_days: int | None


@dataclass(frozen=True)
class ParsedPriceList:
    source: str
    currency: str | None
    rows: list[ParsedRow]


def normalise(text: str) -> str:
    """Lower-case, accents folded, symbols collapsed to single spaces."""
    folded = (
        text.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )
    return re.sub(r"[\s_\-./#:()]+", " ", folded).strip()


def find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    """The first row that names a code column and a price column, with its column map."""
    for index, row in enumerate(rows[:HEADER_ROWS_TO_SCAN]):
        mapping: dict[str, int] = {}
        for column, cell in enumerate(row):
            text = normalise(cell)
            if not text:
                continue
            for field, words in HEADERS.items():
                if field in mapping:
                    continue
                if any(text == w or text.startswith(w + " ") for w in words):
                    mapping[field] = column
                    break
        if "code" in mapping and "price" in mapping:
            return index, mapping
    return None


def parse_number(text: str) -> float | None:
    """``1,234.50`` / ``1.234,50`` / ``12,5`` / ``USD 98.50`` -> a float, or None."""
    cleaned = re.sub(r"[^\d,.\-]", "", text or "")
    if not cleaned or not re.search(r"\d", cleaned):
        return None
    if "," in cleaned and "." in cleaned:
        # the last separator is the decimal one
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        cleaned = head.replace(",", "") + "." + tail if len(tail) <= 2 else cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def currency_in(text: str) -> str | None:
    upper = text.upper()
    for code in CURRENCIES:
        if re.search(rf"\b{code}\b", upper):
            return code
    if "$" in text and "S/" not in text:
        return "USD"
    if "S/" in text:
        return "PEN"
    return None


def parse_price_list(table: TableData) -> ParsedPriceList | None:
    """The rows of a price list, or None when the table does not look like one."""
    header = find_header(table.rows)
    if header is None:
        return None
    start, columns = header
    header_row = table.rows[start]
    sheet_currency = currency_in(header_row[columns["price"]]) or currency_in(" ".join(header_row))
    rows: list[ParsedRow] = []
    for raw in table.rows[start + 1 :]:
        code = _cell(raw, columns.get("code")).strip()
        price = parse_number(_cell(raw, columns.get("price")))
        if not code or price is None or price <= 0:
            continue
        currency = (
            currency_in(_cell(raw, columns["currency"])) if "currency" in columns else None
        ) or currency_in(_cell(raw, columns.get("price")))
        min_qty = parse_number(_cell(raw, columns.get("min_qty"))) if "min_qty" in columns else 0
        lead = (
            parse_number(_cell(raw, columns.get("lead_days"))) if "lead_days" in columns else None
        )
        rows.append(
            ParsedRow(
                code=code,
                description=_cell(raw, columns.get("description")).strip(),
                price=round(price, 4),
                currency=currency or sheet_currency,
                min_qty=float(min_qty or 0),
                lead_days=int(lead) if lead is not None and lead >= 0 else None,
            )
        )
        if len(rows) >= MAX_ROWS:
            break
    return ParsedPriceList(source=table.name, currency=sheet_currency, rows=rows) if rows else None


def _cell(row: list[str], column: int | None) -> str:
    if column is None or column >= len(row):
        return ""
    return row[column]


def code_of_product(name: str) -> str | None:
    """``[CBEA-LHN] Válvula…`` -> ``CBEA-LHN``."""
    found = CODE_IN_NAME.search(name or "")
    return found.group(1).strip().upper() if found else None


ProductLookup = Callable[[str], tuple[int, str] | None]


def build_diff(
    parsed: ParsedPriceList,
    *,
    partner_id: int,
    partner_name: str,
    current: list[dict[str, Any]],
    lookup: ProductLookup,
) -> PriceListDiff:
    """Proposed price updates: each parsed row against what Odoo holds for the supplier.

    ``current`` is the supplier's price history (``product``, ``product_code``, ``price``,
    ``currency``, ``min_qty``). A row matches through our product code (in the product
    name) or through the supplier's own code already on our price list; ``lookup``
    resolves a code to a product we buy.
    """
    by_our_code: dict[str, dict[str, Any]] = {}
    by_their_code: dict[str, dict[str, Any]] = {}
    for known in current:
        our = code_of_product(str(known.get("product") or ""))
        if our and our not in by_our_code:
            by_our_code[our] = known
        theirs = str(known.get("product_code") or "").strip().upper()
        if theirs and theirs not in by_their_code:
            by_their_code[theirs] = known
    rows: list[PriceListRow] = []
    unmatched = 0
    for row in parsed.rows:
        key = row.code.strip().upper()
        entry = by_our_code.get(key) or by_their_code.get(key)
        our_code: str | None = key if key in by_our_code else None
        if our_code is None and entry is not None:
            our_code = code_of_product(str(entry.get("product") or ""))
        product = lookup(our_code or key)
        if product is None and entry is not None and entry.get("product_id"):
            product = (int(entry["product_id"]), str(entry.get("product") or key))
        matched = product is not None
        if not matched:
            unmatched += 1
        current_price = float(entry["price"]) if entry and entry.get("price") is not None else None
        change = (
            round((row.price - current_price) / current_price * 100, 2) if current_price else None
        )
        rows.append(
            PriceListRow(
                code=row.code,
                description=row.description,
                product_id=product[0] if product else None,
                product=product[1] if product else None,
                current_price=current_price,
                new_price=row.price,
                currency=row.currency
                or (str(entry.get("currency")) if entry and entry.get("currency") else None),
                min_qty=row.min_qty,
                lead_days=row.lead_days,
                change_pct=change,
                matched=matched,
            )
        )
    return PriceListDiff(
        partner_id=partner_id,
        partner_name=partner_name,
        source=parsed.source,
        currency=parsed.currency,
        rows=rows,
        unmatched=unmatched,
    )


__all__ = [
    "ParsedPriceList",
    "ParsedRow",
    "build_diff",
    "code_of_product",
    "currency_in",
    "find_header",
    "parse_number",
    "parse_price_list",
]
