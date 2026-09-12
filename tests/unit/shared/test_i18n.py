"""The human-facing catalog: every key has both languages, English is the fallback."""

from __future__ import annotations

import pytest

from sc_core.i18n import MESSAGES, SUPPORTED, language_name, normalize, t


def test_every_message_has_every_language_and_the_same_placeholders() -> None:
    import string

    for key, entry in MESSAGES.items():
        assert set(entry) == set(SUPPORTED), key
        fields = {
            tuple(sorted(name for _, name, _, _ in string.Formatter().parse(text) if name))
            for text in entry.values()
        }
        assert len(fields) == 1, f"{key}: placeholders differ between languages"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("es_PE", "es"),
        ("es", "es"),
        ("en_US", "en"),
        ("en-GB", "en"),
        ("fr_FR", "en"),
        (None, "en"),
        ("", "en"),
    ],
)
def test_normalize_odoo_language_codes(code: str | None, expected: str) -> None:
    assert normalize(code) == expected


def test_normalize_falls_back_to_the_given_default() -> None:
    assert normalize(None, default="es") == "es" and normalize("pt_BR", default="es") == "es"


def test_t_formats_in_the_language_and_falls_back_to_english() -> None:
    assert t("send.summary", "es", label="cotización", partner="ACME", po="P1") == (
        "cotización enviada a ACME por P1"
    )
    assert (
        t("send.summary", "en", label="RFQ", partner="ACME", po="P1") == "RFQ sent to ACME for P1"
    )
    assert t("signature", "es_PE") == "Equipo de Compras"  # Odoo codes are accepted
    assert t("signature", None) == "Purchasing Team"
    assert language_name("es") == "Spanish" and language_name("en") == "English"


def test_unknown_key_is_an_error() -> None:
    with pytest.raises(KeyError):
        t("nope", "en")
