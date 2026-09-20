"""Supplier questions: factual answers go alone by policy, decisions never; disputes escalate."""

from __future__ import annotations

from typing import Any

from sc_core.graph import cleared
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import Classification, SupplierCommsTask
from sc_core.schema.autonomy import AutonomyPolicy, AutonomyRule, RuleConditions
from supplier_comms.domain.models import AnswerOutput
from supplier_comms.testing import FakePorts
from tests.unit.graph.toy import FakeApprovalPorts

MSG = "AAMk-question-1"


def _task(case_id: str) -> SupplierCommsTask:
    return SupplierCommsTask(
        kind="handle_inbound", case_id=case_id, po_name="P00015", graph_message_id=MSG
    )


def answers_alone() -> Any:
    """A rule that lets factual answers out without a person."""
    return AutonomyPolicy(
        rules=[
            AutonomyRule(
                id="answers",
                kind="send_email",
                when=RuleConditions(email_kinds=["answer"]),
                level="auto_notice",
            )
        ]
    ).provider()


async def test_a_factual_answer_cites_its_sources_and_goes_alone_by_policy(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "¿A qué dirección entregamos y cuál es la condición de pago?"
    ports.terms[7] = {"payment_terms": "30 días", "delivery_address": "Av. Industrial 123, Lima"}
    chat.responses.extend(
        [
            Classification(kind="question", confidence=0.92, reason="pregunta dirección y pago"),
            "Leo los términos de la orden.",
            AnswerOutput(
                subject="Re: consulta sobre la orden",
                html_body=(
                    "<p>Entregar en Av. Industrial 123, Lima; pago a 30 días.</p>"
                    "<p>Referencia: orden P00015.</p><p>Equipo de Compras</p>"
                ),
                factual=True,
                sources=["P00015 terms: payment 30 días", "P00015 delivery address"],
            ),
        ]
    )
    agent = make_agent(policy=answers_alone())
    result = await agent.run(_task("case_ans1"))
    # no person in the loop: the rule sent it, the sources travel with the result
    assert result.status == "sent" and approval_ports.created == []
    assert result.outbound is not None and result.outbound.kind == "answer"
    assert result.answer is not None and result.answer.factual
    assert result.answer.sources == ["P00015 terms: payment 30 días", "P00015 delivery address"]
    draft = ports.drafts["reply1"]
    assert isinstance(draft, dict) and draft["reply_to"] == MSG
    assert ports.sent_ids == ["reply1"]
    # the model saw the supplier's question and could read the order terms tool
    assert "dirección" in chat.calls[1].messages[1]["contents"][0]["text"]
    assert any("get_order_terms" in str(tool) for tool in chat.calls[1].options["tools"])
    assert cleared(await agent.snapshot("case_ans1"))


async def test_a_question_that_needs_a_decision_always_waits_for_a_person(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "¿Podemos entregar 30 en vez de 20 metros al mismo precio?"
    chat.responses.extend(
        [
            Classification(kind="question", confidence=0.9, reason="pide cambiar la cantidad"),
            "Es un cambio de cantidad.",
            AnswerOutput(
                subject="Re: consulta sobre la orden",
                html_body="<p>Lo revisamos internamente y les confirmamos.</p><p>Equipo</p>",
                factual=False,
                sources=[],
                reason="the supplier asks to change the ordered quantity",
            ),
        ]
    )
    # the same permissive policy: an answer that is not factual still pauses
    agent = make_agent(policy=answers_alone())
    paused = await agent.run(_task("case_ans2"))
    assert paused.status == "awaiting_approval"
    created = approval_ports.created[0]
    assert created["kind"] == "send_email"
    assert created["payload"]["facts"]["email_kind"] == "reply"
    assert (
        created["payload"]["answer"]["reason"] == "the supplier asks to change the ordered quantity"
    )
    assert paused.answer is not None and not paused.answer.factual
    done = await agent.resume("case_ans2", {"approval_id": 101, "status": "approved"})
    assert done.status == "sent" and done.outbound is not None and done.outbound.kind == "reply"


async def test_a_dispute_is_escalated_without_a_draft(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    ports.inbound[MSG] = "Rechazamos la devolución y exigimos el pago de la factura pendiente."
    chat.responses.append(
        Classification(kind="dispute", confidence=0.88, reason="rechaza devolución y reclama pago")
    )
    agent = make_agent(policy=answers_alone())
    result = await agent.run(_task("case_disp"))
    assert result.status == "escalated" and result.outcome.approval_id == 101
    created = approval_ports.created[0]
    assert created["kind"] == "escalation" and created["po_id"] == 7
    assert "dispute on P00015" in created["summary"]
    assert created["payload"]["reason"] == "rechaza devolución y reclama pago"
    assert ports.drafts == {} and len(chat.calls) == 1  # nothing drafted, no second model call
    assert cleared(await agent.snapshot("case_disp"))
