"""Tests for sc_core.mail.normalize on realistic supplier replies."""

from pathlib import Path

import pytest

from sc_core.mail import normalize

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "mail"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_outlook_spanish_reply_keeps_only_the_answer() -> None:
    text = normalize.text(load("reply_eta_es_outlook.txt"))
    assert text.startswith("Estimado Joaquín,")
    assert "20 de octubre de 2026" in text and "15 de octubre" in text
    assert "Carlos Mendoza" not in text  # signature cut at "Saludos cordiales,"
    assert "De:" not in text and "Solicitud de actualización" not in text  # quoted header block


def test_gmail_spanish_quote_reply() -> None:
    text = normalize.text(load("reply_quote_es_gmail.txt"))
    assert "285.00 USD" in text and "plazo 12 días" in text
    assert "Atentamente" not in text and "María Torres" not in text
    assert "escribió" not in text and ">" not in text and "x 20" not in text


def test_english_mobile_reply() -> None:
    text = normalize.text(load("reply_en_mobile.txt"))
    assert text == "Hi,\n\nShipment left today. Tracking DHL 1234567890, ETA Oct 18."


def test_short_reply_without_markers_is_untouched() -> None:
    assert normalize.text(load("reply_short_no_markers.txt")) == (
        "Recibido, procedemos con el despacho mañana."
    )


def test_html_body_is_converted() -> None:
    body = (
        "<html><head><style>p{color:red}</style></head><body>"
        "<div>Confirmado&nbsp;para el <b>20/10</b>.</div><p>Gracias &amp; saludos</p>"
        "<br><div>-- </div><div>Firma</div>"
        "<blockquote>El lunes escribió:<br>&gt; texto citado</blockquote></body></html>"
    )
    assert normalize.text(body) == "Confirmado para el 20/10.\n\nGracias & saludos"
    assert normalize.is_html(body) and not normalize.is_html("plain text")


@pytest.mark.parametrize(
    "closing",
    ["Saludos cordiales,", "Atentamente", "Best regards,", "Kind regards", "Regards,", "Gracias!"],
)
def test_common_closings_cut_signature(closing: str) -> None:
    body = f"Llega el 20 de octubre.\n\n{closing}\nAna\nEmpresa SAC"
    assert normalize.text(body) == "Llega el 20 de octubre."


def test_first_line_is_never_cut() -> None:
    assert normalize.text("Gracias\nLlega el 20") == "Gracias\nLlega el 20"


def test_wrapped_wrote_marker_and_underscore_rule() -> None:
    body = "Ok\n\nEl vie, 11 sept 2026 a las 16:40, Alguien\nescribió:\n\ntexto viejo"
    assert normalize.text(body) == "Ok"
    assert normalize.text("Listo\n________________\nDe: x\nPara: y") == "Listo"


def test_from_line_without_header_block_is_kept() -> None:
    body = "De: acuerdo con lo conversado, enviamos el 20.\nGracias"
    assert "acuerdo" in normalize.text(body)


def test_empty_and_whitespace() -> None:
    assert normalize.text(None) == "" and normalize.text("   \n\n ") == ""
    assert normalize.text("a  b \t c\n\n\n\nd") == "a b c\n\nd"
