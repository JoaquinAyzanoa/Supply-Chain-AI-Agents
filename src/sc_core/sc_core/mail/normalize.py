"""Turn an email body into the text that matters: the sender's own words.

Graph's ``uniqueBody`` already strips most quoted history, but clients vary
(mobile apps, Gmail, forwards), so this is the safety net before the text
reaches a linker or a model. It removes, in order:

1. HTML markup (when the body is HTML), keeping line structure.
2. Quoted lines (``>`` prefixes).
3. Everything from the first reply marker on: Outlook's ``De:/From:`` header
   block, ``-----Mensaje original-----``, ``El ... escribió:``,
   ``On ... wrote:``.
4. The signature: the RFC ``-- `` separator, mobile taglines, and common
   Spanish and English closings.

The result is trimmed and whitespace-normalised. It is deliberately
conservative: when in doubt, text is kept rather than dropped.
"""

from __future__ import annotations

import html
import re

_BLOCK_TAGS = re.compile(r"</?(p|div|br|tr|li|h[1-6]|table|blockquote)[^>]*>", re.IGNORECASE)
_STRIP_BLOCKS = re.compile(r"<(script|style|head)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_HTML_HINT = re.compile(r"<\s*(html|body|div|p|br|table|span)\b", re.IGNORECASE)

# A line that starts the quoted part of a reply. Matched on stripped lines.
_REPLY_MARKERS = [
    re.compile(
        r"^-{2,}\s*(original message|mensaje original|forwarded message|mensaje reenviado)"
        r"\s*-{2,}$",
        re.I,
    ),
    re.compile(r"^(el|on)\s.{3,120}?(escribi[oó]|wrote)\s*:\s*$", re.I),
    re.compile(r"^(el|on)\s.{3,120}$", re.I),  # first line of a wrapped "El ..., X escribió:"
    re.compile(r"^_{5,}$"),
]
# Outlook-style header block: "De:"/"From:" followed within a few lines by a recipient line.
_HEADER_FROM = re.compile(r"^(de|from)\s*:", re.I)
_HEADER_FOLLOW = re.compile(r"^(para|to|enviado( el)?|sent|asunto|subject|cc)\s*:", re.I)

_SIGNATURE_MARKERS = [
    re.compile(r"^--\s*$"),
    re.compile(r"^(enviado desde|sent from|get outlook for|obtener outlook para)\b", re.I),
    re.compile(
        r"^(saludos( cordiales)?|atentamente|atte\.?|cordialmente|un saludo|quedo atento|"
        r"best regards|kind regards|regards|sincerely|thanks( and regards)?|thank you|gracias)"
        r"\s*[,.!]?\s*$",
        re.I,
    ),
]

_MAX_HEADER_LOOKAHEAD = 4


def is_html(body: str) -> bool:
    return bool(_HTML_HINT.search(body or ""))


def html_to_text(body: str) -> str:
    text = _STRIP_BLOCKS.sub("", body)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAGS.sub("", text)
    text = html.unescape(text)
    return text.replace("\xa0", " ")


def text(body: str | None, *, html_body: bool | None = None) -> str:
    """The sender's own text: no markup, no quoted history, no signature."""
    if not body:
        return ""
    raw = html_to_text(body) if (html_body if html_body is not None else is_html(body)) else body
    lines = [line.rstrip() for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = _drop_quoted(lines)
    lines = _cut_at_reply_marker(lines)
    lines = _cut_signature(lines)
    return _collapse(lines)


def _drop_quoted(lines: list[str]) -> list[str]:
    return [line for line in lines if not line.lstrip().startswith(">")]


def _cut_at_reply_marker(lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker.match(stripped) for marker in _REPLY_MARKERS[:2]) or _REPLY_MARKERS[3].match(
            stripped
        ):
            return lines[:index]
        if _REPLY_MARKERS[2].match(stripped) and _wrapped_wrote(lines, index):
            return lines[:index]
        if _HEADER_FROM.match(stripped) and _header_block_follows(lines, index):
            return lines[:index]
    return lines


def _wrapped_wrote(lines: list[str], index: int) -> bool:
    """``El vie, 12 sept 2026 a las 10:00, Ventas (<v@x.com>)`` + next line ``escribió:``."""
    for follow in lines[index + 1 : index + 3]:
        if re.search(r"(escribi[oó]|wrote)\s*:\s*$", follow.strip(), re.I):
            return True
    return False


def _header_block_follows(lines: list[str], index: int) -> bool:
    window = lines[index + 1 : index + 1 + _MAX_HEADER_LOOKAHEAD]
    return any(_HEADER_FOLLOW.match(follow.strip()) for follow in window)


def _cut_signature(lines: list[str]) -> list[str]:
    # Scan from the top so the earliest closing wins, but never cut the very first line.
    for index, line in enumerate(lines):
        if index == 0:
            continue
        stripped = line.strip()
        if stripped and any(marker.match(stripped) for marker in _SIGNATURE_MARKERS):
            return lines[:index]
    return lines


def _collapse(lines: list[str]) -> str:
    joined = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in lines)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()
