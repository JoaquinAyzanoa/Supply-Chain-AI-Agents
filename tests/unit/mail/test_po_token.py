"""Tests for sc_core.mail.po_token."""

import pytest

from sc_core.mail import po_token


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("[P00015] Solicitud de cotización", "P00015"),
        ("RE: [P00015] Solicitud de cotización", "P00015"),
        ("Fwd: re: [PR00001] licitación", "PR00001"),
        ("sin token", None),
        ("", None),
        (None, None),
        ("[p00015] lower case is not a token", None),
        ("[ABCDEF123456] prefix too long", None),
        ("[P12] too short", None),
    ],
)
def test_parse(subject: str | None, expected: str | None) -> None:
    assert po_token.parse(subject) == expected


def test_make_and_validation() -> None:
    assert po_token.make("p00015") == "[P00015]"
    with pytest.raises(ValueError):
        po_token.make("order 15")


def test_tag_subject_is_idempotent() -> None:
    assert po_token.tag_subject("Solicitud", "P00015") == "[P00015] Solicitud"
    assert po_token.tag_subject("RE: [P00015] Solicitud", "P00015") == "RE: [P00015] Solicitud"
    assert po_token.tag_subject("  ", "P00015") == "[P00015]"


def test_headers_for() -> None:
    assert po_token.headers_for("p00015") == {"x-sc-po": "P00015"}
    assert po_token.headers_for("P00015", "case_1") == {"x-sc-po": "P00015", "x-sc-case": "case_1"}
