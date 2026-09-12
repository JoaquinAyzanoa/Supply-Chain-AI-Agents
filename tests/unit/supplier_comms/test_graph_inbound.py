"""Inbound flow: ETA update, quotation with a currency mismatch, out-of-office, question."""

from __future__ import annotations

from datetime import date
from typing import Any

from sc_core.graph import cleared
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import Classification, QuotationData, QuotedLine, SupplierCommsTask
from supplier_comms.models import DraftOutput
from supplier_comms.nodes.propose import build_proposal
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
    assert "manual review" in ports.notes[-1][1]


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
            DraftOutput(
                subject="Re: consulta sobre la orden",
                html_body="<p>Son 20 metros.</p><p>Equipo de Compras</p>",
            ),
        ]
    )
    agent = make_agent()
    paused = await agent.run(_task("case_qn"))
    assert paused.status == "awaiting_approval"
    assert approval_ports.created[0]["kind"] == "send_email"
    assert "metros o en rollos" in chat.calls[1].messages[1]["contents"][0]["text"]
    draft = ports.drafts["reply1"]
    assert isinstance(draft, dict) and draft["reply_to"] == MSG
    assert draft["headers"]["x-sc-po"] == "P00015"
    sent = await agent.resume("case_qn", {"approval_id": 101, "status": "approved"})
    assert sent.status == "sent" and ports.sent_ids == ["reply1"]
    assert sent.outbound is not None and sent.outbound.kind == "reply"


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
    assert kinds[0] == ("date_planned", True, "fecha interpretada con baja confianza")
    assert kinds[-1][0] == "price" and kinds[-1][1] and "not matched" in (kinds[-1][2] or "")
    assert proposal.applicable == [] and '"20/10"' in proposal.summary
