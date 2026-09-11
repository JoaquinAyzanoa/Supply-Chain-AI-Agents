"""Tests for sc_core.mail.pdf with hand-built PDFs (no external fixtures needed)."""

from __future__ import annotations

from sc_core.mail import pdf


def make_pdf(pages: list[str]) -> bytes:
    """A minimal but valid PDF with one Helvetica text line per page."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_obj = 3 + 2 * len(pages)
    for i, line in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def test_extracts_text_from_pages() -> None:
    data = make_pdf(["COTIZACION 4711", "Precio 285.00 USD"])
    text = pdf.extract_text(data)
    assert "COTIZACION 4711" in text and "285.00 USD" in text
    assert text.index("COTIZACION") < text.index("Precio")


def test_page_limit_truncates() -> None:
    data = make_pdf([f"Pagina {i}" for i in range(1, 6)])
    text = pdf.extract_text(data, max_pages=2)
    assert "Pagina 2" in text and "Pagina 3" not in text


def test_size_limit_skips() -> None:
    data = make_pdf(["x"])
    assert pdf.extract_text(data, max_bytes=len(data) - 1) == ""


def test_garbage_and_empty_return_empty() -> None:
    assert pdf.extract_text(b"") == ""
    assert pdf.extract_text(b"this is not a pdf at all") == ""
    assert pdf.extract_text(b"%PDF-1.4\n%%EOF") == ""
