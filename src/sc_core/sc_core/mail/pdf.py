"""Text of PDF attachments (quotations, invoices), extracted in memory.

Limits protect the process from hostile or huge files: a byte cap before
parsing and a page cap during extraction. Extraction never raises for a bad
file; it returns an empty string and logs why, because a supplier's odd PDF
must not stop the rest of the message from being handled. Scanned PDFs
(images only) also yield an empty string; OCR is out of scope for now.
"""

from __future__ import annotations

from io import BytesIO

from loguru import logger
from pypdf import PdfReader
from pypdf.errors import PyPdfError

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_BYTES = 10_000_000


def extract_text(
    data: bytes, *, max_pages: int = DEFAULT_MAX_PAGES, max_bytes: int = DEFAULT_MAX_BYTES
) -> str:
    """Concatenated page text, pages separated by a blank line. Empty when unusable."""
    if not data:
        return ""
    if len(data) > max_bytes:
        logger.bind(size=len(data), limit=max_bytes).warning("pdf skipped: larger than the limit")
        return ""
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")  # many "protected" PDFs open with an empty password
            except Exception:  # noqa: BLE001 - treat as unreadable
                logger.warning("pdf skipped: encrypted")
                return ""
        pages = []
        for index, page in enumerate(reader.pages):
            if index >= max_pages:
                logger.bind(limit=max_pages).warning("pdf truncated at the page limit")
                break
            pages.append((page.extract_text() or "").strip())
    except (PyPdfError, ValueError, KeyError, TypeError, RecursionError) as exc:
        logger.bind(reason=type(exc).__name__).warning("pdf skipped: could not be parsed")
        return ""
    return "\n\n".join(p for p in pages if p).strip()
