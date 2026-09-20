"""Inbound flow: ETA update, quotation with a currency mismatch, out-of-office, question."""

from __future__ import annotations

from datetime import date
from typing import Any

from sc_core.graph import cleared
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import Classification, QuotationData, QuotedLine, SupplierCommsTask
from supplier_comms.domain.models import AnswerOutput
from supplier_comms.graph.nodes.propose import build_proposal
from supplier_comms.testing import FakePorts, demo_context
from tests.unit.graph.toy import FakeApprovalPorts

MSG = "AAMk-reply-1"


def _task(case_id: str) -> SupplierCommsTask:
    return SupplierCommsTask(
        kind="handle_inbound", case_id=case_id, po_name="P00015", graph_message_id=MSG
    )


async def test_eta_update_proposes_dates_then_applies_after_approval(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "Estimados, confirmamos que la entrega será el 15 de octubre. Saludos."
    chat.responses.extend(
        [
            Classification(kind="eta_update", confidence=0.95, reason="confirma fecha"),
            QuotationData(
                eta_date_raw="15 de octubre", eta_date=date(2026, 10, 15), confidence=0.9
            ),
        ]
    )
    agent = make_agent()
    paused = await agent.run(_task("case_eta"))
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101
    assert paused.classification is not None and paused.classification.kind == "eta_update"
    assert paused.proposal is not None and len(paused.proposal.changes) == 2
    assert all(
        c.field == "date_planned" and c.after == "2026-10-15" for c in paused.proposal.changes
    )
    assert (
        "15 de octubre" in chat.calls[0].messages[1]["contents"][0]["text"]
    )  # body reached the model
    payload = approval_ports.created[0]["payload"]
    assert approval_ports.created[0]["kind"] == "po_change" and payload["needs_review"] == 0
    assert len(payload["changes"]) == 2

    applied = await agent.resume(
        "case_eta", {"approval_id": 101, "status": "approved", "resolved_by": "ana"}
    )
    assert applied.status == "applied"
    assert [c["line_id"] for c in ports.date_changes] == [31, 32]
    assert all(
        c["date"] == "2026-10-15" and c["run_id"] == applied.run_id for c in ports.date_changes
    )
    assert ports.eta_meta == [{"po_id": 7, "confidence": 0.9}]
    assert ports.price_upserts == []
    assert "2 change(s)" in ports.notes[-1][1] and "ana" in ports.notes[-1][1]
    assert cleared(await agent.snapshot("case_eta"))


async def test_partial_acceptance_applies_only_the_ticked_lines(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "Estimados, confirmamos que la entrega será el 15 de octubre. Saludos."
    chat.responses.extend(
        [
            Classification(kind="eta_update", confidence=0.95, reason="confirms date"),
            QuotationData(
                eta_date_raw="15 de octubre", eta_date=date(2026, 10, 15), confidence=0.9
            ),
        ]
    )
    agent = make_agent()
    await agent.run(_task("case_part"))
    applied = await agent.resume(
        "case_part",
        {
            "approval_id": 101,
            "status": "approved",
            "resolved_by": "ana",
            "details": {"accepted_line_ids": [32]},
        },
    )
    assert applied.status == "applied"
    assert [c["line_id"] for c in ports.date_changes] == [32]
    assert "1 change(s)" in ports.notes[-1][1] and "1 pending" in applied.outcome.summary


async def test_quotation_with_currency_mismatch_flags_that_line(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "Cotizamos: bomba 480 PEN c/u, manguera 1.20 USD/m, entrega en 10 días."
    chat.responses.extend(
        [
            Classification(kind="quotation", confidence=0.9, reason="precios"),
            QuotationData(
                lines=[
                    QuotedLine(
                        po_line_id=31,
                        description="Bomba",
                        unit_price=480.0,
                        currency="PEN",
                        lead_days=10,
                    ),
                    QuotedLine(
                        po_line_id=32, description="Manguera", unit_price=1.2, currency="USD"
                    ),
                ],
                confidence=0.9,
            ),
        ]
    )
    agent = make_agent()
    paused = await agent.run(_task("case_q"))
    assert paused.status == "awaiting_approval"
    proposal = paused.proposal
    assert proposal is not None and proposal.needs_review
    fields = [(c.po_line_id, c.field, c.needs_review) for c in proposal.changes]
    assert fields == [(31, "price", False), (31, "lead_days", False), (32, "price", True)]
    assert "moneda" in (proposal.changes[2].review_reason or "")

    applied = await agent.resume("case_q", {"approval_id": 101, "status": "approved"})
    assert applied.status == "applied" and "1 pending" in applied.outcome.summary
    assert ports.price_upserts == [
        {
            "partner_id": 42,
            "product_tmpl_id": 1001,
            "product_id": 101,
            "price": 480.0,
            "currency_id": 3,
            "min_qty": 0.0,
            "lead_days": 10,
        }
    ]
    assert ports.date_changes == []
    assert ports.line_prices == []  # a confirmed order keeps its agreed price
    assert "manual review" in ports.notes[-1][1]


async def test_a_quote_on_a_sent_rfq_lands_on_the_line(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    """The comparison and the counter-offer read the RFQ line: the quote must be there."""
    ports.contexts["P00015"] = demo_context().model_copy(update={"state": "sent"})
    ports.inbound[MSG] = "Cotizamos: bomba 480 PEN c/u, entrega en 10 días."
    chat.responses.extend(
        [
            Classification(kind="quotation", confidence=0.9, reason="precios"),
            QuotationData(
                lines=[
                    QuotedLine(
                        po_line_id=31,
                        description="Bomba",
                        unit_price=480.0,
                        currency="PEN",
                        lead_days=10,
                    )
                ],
                confidence=0.9,
            ),
        ]
    )
    agent = make_agent()
    paused = await agent.run(_task("case_rfq"))
    assert paused.status == "awaiting_approval"
    applied = await agent.resume("case_rfq", {"approval_id": 101, "status": "approved"})
    assert applied.status == "applied"
    assert ports.line_prices == [{"line_id": 31, "price": 480.0, "run_id": applied.run_id}]
    assert [u["price"] for u in ports.price_upserts] == [480.0]  # and the price list too


async def test_out_of_office_is_no_action_without_approval(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "Estoy fuera de la oficina hasta el lunes."
    chat.responses.append(Classification(kind="other", confidence=0.99, reason="fuera de oficina"))
    agent = make_agent()
    result = await agent.run(_task("case_ooo"))
    assert result.status == "no_action" and "fuera de oficina" in result.outcome.summary
    assert approval_ports.created == [] and len(chat.calls) == 1
    snapshot = await agent.snapshot("case_ooo")
    assert cleared(snapshot) and snapshot["outcome"]["status"] == "no_action"


async def test_question_is_answered_in_the_thread(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "¿La cantidad de mangueras es en metros o en rollos?"
    chat.responses.extend(
        [
            Classification(kind="question", confidence=0.9, reason="pregunta unidad"),
            "Respondo con la unidad de la orden.",
            AnswerOutput(
                subject="Re: consulta sobre la orden",
                html_body=(
                    "<p>Son 20 metros. Referencia: P00015, línea 1.</p><p>Equipo de Compras</p>"
                ),
                factual=True,
                sources=["P00015 line 1: 20 m"],
            ),
        ]
    )
    agent = make_agent()
    paused = await agent.run(_task("case_qn"))
    assert paused.status == "awaiting_approval"
    created = approval_ports.created[0]
    assert created["kind"] == "send_email" and created["payload"]["facts"]["email_kind"] == "answer"
    assert created["payload"]["answer"] == {
        "factual": True,
        "sources": ["P00015 line 1: 20 m"],
        "reason": None,
    }
    assert "metros o en rollos" in chat.calls[1].messages[1]["contents"][0]["text"]
    draft = ports.drafts["reply1"]
    assert isinstance(draft, dict) and draft["reply_to"] == MSG
    assert draft["headers"]["x-sc-po"] == "P00015"
    sent = await agent.resume("case_qn", {"approval_id": 101, "status": "approved"})
    assert sent.status == "sent" and ports.sent_ids == ["reply1"]
    assert sent.outbound is not None and sent.outbound.kind == "answer"
    assert sent.answer is not None and sent.answer.factual


async def test_nothing_new_is_no_action_without_approval(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "Confirmamos la fecha del 1 de octubre."
    chat.responses.extend(
        [
            Classification(kind="eta_update", confidence=0.9, reason="confirma"),
            QuotationData(eta_date_raw="1 de octubre", eta_date=date(2026, 10, 1), confidence=0.9),
        ]
    )
    result = await make_agent().run(_task("case_same"))
    assert result.status == "no_action" and approval_ports.created == []


def test_build_proposal_low_confidence_and_unmapped_line() -> None:
    ctx = demo_context()
    data = QuotationData(
        lines=[QuotedLine(description="Válvula", unit_price=99.0, currency="PEN")],
        eta_date_raw="20/10",
        eta_date=date(2026, 10, 20),
        confidence=0.5,
    )
    proposal = build_proposal(ctx, data)
    kinds = [(c.field, c.needs_review, c.review_reason) for c in proposal.changes]
    assert kinds[0] == ("date_planned", True, "date read with low confidence")
    assert kinds[-1][0] == "price" and kinds[-1][1] and "not matched" in (kinds[-1][2] or "")
    assert proposal.applicable == [] and '"20/10"' in proposal.summary


async def test_a_split_delivery_gets_a_schedule_and_splits_the_line_on_approval(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    from sc_core.schema.a2a import DeliverySplit

    ports.inbound[MSG] = (
        "Bomba: 1 unidad llega el 15/10, la otra el 20/11. Mangueras: todas el 15/10."
    )
    chat.responses.extend(
        [
            Classification(kind="eta_update", confidence=0.95, reason="fechas por línea"),
            QuotationData(
                lines=[
                    QuotedLine(
                        po_line_id=31,
                        description="Bomba hidráulica 2HP",
                        deliveries=[
                            DeliverySplit(qty=1, date=date(2026, 10, 15), date_raw="15/10"),
                            DeliverySplit(qty=1, date=date(2026, 11, 20), date_raw="20/11"),
                        ],
                        confidence=0.9,
                    ),
                    QuotedLine(
                        po_line_id=32,
                        description='Manguera 1/2"',
                        eta_date=date(2026, 10, 15),
                        eta_date_raw="15/10",
                        confidence=0.9,
                    ),
                ],
                confidence=0.9,
            ),
        ]
    )
    agent = make_agent()
    paused = await agent.run(_task("case_split"))
    assert paused.status == "awaiting_approval" and paused.proposal is not None
    by_line = {c.po_line_id: c for c in paused.proposal.changes}
    assert by_line[31].after == "2026-10-15" and not by_line[31].needs_review
    assert [(p.qty, str(p.date)) for p in by_line[31].schedule] == [
        (1.0, "2026-10-15"),
        (1.0, "2026-11-20"),
    ]
    assert by_line[32].after == "2026-10-15" and by_line[32].schedule == []
    assert "1 split delivery(ies)" in paused.proposal.summary
    applied = await agent.resume(
        "case_split", {"approval_id": 101, "status": "approved", "resolved_by": "ana"}
    )
    assert applied.status == "applied"
    # the split line is cut in two in Odoo; the other line just moves its date
    assert ports.splits == [
        {
            "line_id": 31,
            "parts": [(1.0, "2026-10-15"), (1.0, "2026-11-20")],
            "run_id": applied.run_id,
        }
    ]
    assert [c["line_id"] for c in ports.date_changes] == [32]


def test_split_parts_that_do_not_add_up_are_sent_to_a_person() -> None:
    from sc_core.schema.a2a import DeliverySplit
    from supplier_comms.graph.nodes.propose import build_proposal

    data = QuotationData(
        lines=[
            QuotedLine(
                po_line_id=31,
                description="Bomba",
                deliveries=[
                    DeliverySplit(qty=1, date=date(2026, 10, 15)),
                    DeliverySplit(qty=3, date=date(2026, 11, 20)),
                ],
            )
        ],
        confidence=0.9,
    )
    [change] = build_proposal(demo_context(), data).changes
    assert (
        change.needs_review and change.review_reason == "the parts add up to 4, the line orders 2"
    )
