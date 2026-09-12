"""Linking rules, one test per rule, plus precedence and the unlinked hint."""

from __future__ import annotations

import pytest

from mail_sync.linker import Linker, referenced_message_ids
from sc_core.mail.models import EmailAddress, InboundMessage

from .fakes import FakePorts

SUPPLIER = "ventas.hidraulica.sc@gmail.com"


def _message(
    *,
    subject: str | None = "Re: cotización",
    sender: str | None = SUPPLIER,
    conversation_id: str = "conv1",
) -> InboundMessage:
    return InboundMessage(
        id="AAMk1",
        conversation_id=conversation_id,
        internet_message_id="<reply1@gmail.com>",
        subject=subject,
        sender=EmailAddress(address=sender, name="Proveedor Hidraulica") if sender else None,
    )


@pytest.fixture
def ports() -> FakePorts:
    p = FakePorts()
    p.add_po("P00015", partner_id=42)
    p.add_po("P00016")
    p.contacts[SUPPLIER] = 42
    return p


async def test_rule_1_header(ports: FakePorts) -> None:
    outcome = await Linker(ports).link(_message(subject="hola"), {"X-SC-PO": "p00015"})
    assert outcome.link and outcome.link.rule == "header" and outcome.link.po.name == "P00015"
    assert outcome.link.confidence == "exact"


async def test_rule_2_subject_token(ports: FakePorts) -> None:
    outcome = await Linker(ports).link(_message(subject="RE: [P00016] Orden"), {})
    assert outcome.link and outcome.link.rule == "subject_token"
    assert outcome.link.po.name == "P00016"


async def test_rule_3_conversation(ports: FakePorts) -> None:
    ports.conversations["conv1"] = "P00016"
    outcome = await Linker(ports).link(_message(subject="gracias"), {})
    assert outcome.link and outcome.link.rule == "conversation"
    assert outcome.link.po.name == "P00016"


async def test_rule_4_in_reply_to_and_references(ports: FakePorts) -> None:
    ports.sent_message_ids["<rfq1@outlook.com>"] = "P00016"
    linker = Linker(ports)
    by_reply = await linker.link(
        _message(subject="x", conversation_id="other"), {"In-Reply-To": "<rfq1@outlook.com>"}
    )
    assert by_reply.link and by_reply.link.rule == "in_reply_to"
    by_refs = await linker.link(
        _message(subject="x", conversation_id="other"),
        {"references": "<a@x> <rfq1@outlook.com> <b@x>"},
    )
    assert by_refs.link and by_refs.link.po.name == "P00016"


async def test_rule_5_sender_with_single_open_po(ports: FakePorts) -> None:
    outcome = await Linker(ports).link(_message(subject="sin token", conversation_id="new"), {})
    assert outcome.link and outcome.link.rule == "sender_single_open_po"
    assert outcome.link.confidence == "sender_single_open_po"
    assert outcome.link.po.name == "P00015" and outcome.hint.partner_id == 42


async def test_rule_6_unlinked_with_candidates(ports: FakePorts) -> None:
    ports.add_po("P00017", partner_id=42)
    outcome = await Linker(ports).link(_message(subject="sin token", conversation_id="new"), {})
    assert outcome.link is None
    assert outcome.hint.partner_id == 42 and outcome.hint.open_po_names == ["P00015", "P00017"]


async def test_unknown_sender_is_unlinked_without_partner(ports: FakePorts) -> None:
    outcome = await Linker(ports).link(
        _message(subject="?", sender="nobody@example.com", conversation_id="new"), {}
    )
    assert outcome.link is None and outcome.hint.partner_id is None
    assert outcome.hint.open_po_names == []


async def test_no_sender_at_all(ports: FakePorts) -> None:
    outcome = await Linker(ports).link(_message(subject=None, sender=None, conversation_id="n"), {})
    assert outcome.link is None


async def test_precedence_header_beats_subject_beats_conversation(ports: FakePorts) -> None:
    ports.conversations["conv1"] = "P00016"
    ports.add_po("P00099")
    outcome = await Linker(ports).link(
        _message(subject="[P00099] forwarded"), {"x-sc-po": "P00015"}
    )
    assert outcome.link and outcome.link.rule == "header" and outcome.link.po.name == "P00015"
    outcome = await Linker(ports).link(_message(subject="[P00099] forwarded"), {})
    assert outcome.link and outcome.link.rule == "subject_token"


async def test_unknown_header_or_token_falls_through(ports: FakePorts) -> None:
    ports.conversations["conv1"] = "P00016"
    outcome = await Linker(ports).link(_message(subject="[P99999] x"), {"x-sc-po": "P88888"})
    assert outcome.link and outcome.link.rule == "conversation"


def test_referenced_message_ids_order() -> None:
    ids = referenced_message_ids({"in-reply-to": "<c@x>", "references": "<a@x>\r\n <b@x> <c@x>"})
    assert ids == ["<c@x>", "<b@x>", "<a@x>"]
    assert referenced_message_ids({}) == []
