"""Spreadsheets in attachments become rows of strings; bad files become nothing."""

from __future__ import annotations

from sc_core.mail.models import Attachment
from sc_core.mail.tables import is_spreadsheet, read_tables


def _attachment(
    name: str, data: bytes, content_type: str = "application/octet-stream"
) -> Attachment:
    return Attachment(name=name, content_type=content_type, size=len(data), data=data)


def test_csv_is_sniffed_and_decoded() -> None:
    latin = "Código;Precio\nCBEA-LHN;104,16\n".encode("cp1252")
    [table] = read_tables(_attachment("lista.csv", latin))
    assert table.name == "lista.csv"
    assert table.rows == [["Código", "Precio"], ["CBEA-LHN", "104,16"]]
    [comma] = read_tables(_attachment("l.csv", b"code,price\r\nA-1,2.5\r\n", "text/csv"))
    assert comma.rows == [["code", "price"], ["A-1", "2.5"]]


def test_only_spreadsheets_are_read_and_broken_files_yield_nothing() -> None:
    assert not is_spreadsheet(_attachment("quote.pdf", b"%PDF", "application/pdf"))
    assert is_spreadsheet(_attachment("x.xlsx", b"x"))
    assert read_tables(_attachment("quote.pdf", b"%PDF", "application/pdf")) == []
    assert read_tables(_attachment("broken.xlsx", b"not a workbook")) == []
    assert read_tables(_attachment("big.csv", b"a,b\n" * 10, "text/csv"), max_bytes=8) == []
    assert read_tables(_attachment("empty.csv", b"", "text/csv")) == []


def test_row_limit_caps_a_huge_sheet() -> None:
    data = b"code,price\n" + b"".join(f"C-{i},{i}\n".encode() for i in range(50))
    [table] = read_tables(_attachment("l.csv", data, "text/csv"), max_rows=5)
    assert len(table.rows) == 5
