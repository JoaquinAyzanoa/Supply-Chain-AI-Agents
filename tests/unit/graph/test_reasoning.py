"""Every approval carries its reasons: facts, the rule, alternatives, the counterfactual."""

from __future__ import annotations

from typing import Any

from sc_core.graph import ApprovalGateway, ApprovalRequest, MemoryAutoActions
from sc_core.graph.reasoning import build_reasoning, fact_lines
from sc_core.schema.autonomy import (
    ActionFacts,
    AutonomyPolicy,
    AutonomyRule,
    PolicyDecision,
    Reasoning,
    RuleConditions,
)
from tests.unit.graph.toy import FakeApprovalPorts


def test_facts_become_short_lines_and_defaults_fill_the_rest() -> None:
    facts = ActionFacts(
        partner_id=8,
        partner_name="Proveedor Hidraulica",
        amount=1250.5,
        currency="USD",
        change_days=7,
        confidence=0.82,
        email_kind="request_eta",
    )
    assert fact_lines(facts) == [
        "supplier Proveedor Hidraulica",
        "amount 1,250.50 USD",
        "email kind request_eta",
        "date move 7 day(s)",
        "the agent's confidence 82%",
    ]
    reasoning = build_reasoning(
        kind="po_change",
        facts=facts,
        given=None,
        verdict=PolicyDecision(level="approve", reason="no rule matched"),
        level="approve",
        forced=False,
    )
    assert reasoning.rule == "no rule matched" and reasoning.confidence == 0.82
    assert reasoning.alternatives == [
        "Accept only some of the lines",
        "Reject it and ask the supplier again",
    ]
    assert reasoning.counterfactual is not None
    assert "limited to supplier Proveedor Hidraulica" in reasoning.counterfactual
    assert "allowing date moves up to 7 day(s)" in reasoning.counterfactual
    assert "auto_notice" in reasoning.counterfactual


def test_the_nodes_own_reasons_come_first_and_the_never_automated_kinds_say_so() -> None:
    given = Reasoning(
        facts=["Hidraulica quoted 104.16, Alterna 112.00"],
        alternatives=["Give the valve to Alterna"],
    )
    reasoning = build_reasoning(
        kind="award",
        facts=ActionFacts(amount=1071.0, currency="USD"),
        given=given,
        verdict=PolicyDecision(level="approve", reason="always a person's decision"),
        level="approve",
        forced=False,
    )
    assert reasoning.facts == ["Hidraulica quoted 104.16, Alterna 112.00", "amount 1,071.00 USD"]
    assert reasoning.rule == "always a person's decision"
    assert reasoning.alternatives == ["Give the valve to Alterna"]
    assert reasoning.counterfactual == (
        "A award is always a person's decision; no autonomy rule can change that."
    )
    forced = build_reasoning(
        kind="send_email", facts=None, given=None, verdict=None, level="approve", forced=True
    )
    assert forced.rule == "a person asked to read this first"
    assert (
        forced.counterfactual == "A person asked to read this one first, so no rule was consulted."
    )


def test_the_reasoning_is_written_in_the_language_people_read() -> None:
    facts = ActionFacts(
        partner_id=8,
        partner_name="Proveedor Hidraulica",
        amount=1250.5,
        currency="USD",
        change_days=7,
        confidence=0.82,
    )
    reasoning = build_reasoning(
        kind="po_change",
        facts=facts,
        given=None,
        verdict=PolicyDecision(level="approve", reason="no rule matched"),
        level="approve",
        forced=False,
        lang="es",
    )
    assert reasoning.facts == [
        "proveedor Proveedor Hidraulica",
        "importe 1,250.50 USD",
        "cambio de fecha 7 día(s)",
        "confianza del agente 82%",
    ]
    assert reasoning.rule == "ninguna regla aplica"
    assert reasoning.alternatives[0] == "Aceptar solo algunas líneas"
    assert reasoning.counterfactual is not None
    assert reasoning.counterfactual.startswith("Una regla de autonomía para cambio de orden")
    assert "limitada al proveedor Proveedor Hidraulica" in reasoning.counterfactual
    award = build_reasoning(
        kind="award", facts=None, given=None, verdict=None, level="approve", forced=False, lang="es"
    )
    assert award.rule == "siempre lo decide una persona"
    assert award.counterfactual is not None and "(adjudicación)" in award.counterfactual
    # a rule a person named keeps its note; a rule without one is named, not described in English
    noted = PolicyDecision(level="auto_notice", rule_id="eta", reason="fechas de Hidraulica")
    described = PolicyDecision(level="auto_notice", rule_id="eta", reason="rule eta: emails x")
    for verdict, expected in ((noted, "regla eta: fechas de Hidraulica"), (described, "regla eta")):
        ran = build_reasoning(
            kind="send_email",
            facts=None,
            given=None,
            verdict=verdict,
            level="auto_notice",
            forced=False,
            lang="es",
        )
        assert ran.rule == expected
        assert ran.counterfactual is not None and ran.counterfactual.startswith("Corrió solo")


def _state() -> dict[str, Any]:
    return {"case_id": "case_1", "run_id": "run_1"}


def _gateway(ports: FakeApprovalPorts, store: MemoryAutoActions, policy: AutonomyPolicy) -> Any:
    return ApprovalGateway(
        ports,
        agent_name="toy",
        callback_url="http://toy/approvals/callback",
        callback_secret="s",
        approver_user_id=2,
        deadline_days=2,
        policy=policy.provider(),
        auto_actions=store,
    )


async def test_the_gateway_stores_the_reasoning_on_the_approval_and_on_the_feed() -> None:
    ports, store = FakeApprovalPorts(), MemoryAutoActions()
    policy = AutonomyPolicy(
        rules=[
            AutonomyRule(
                id="trusted-dates",
                kind="po_change",
                when=RuleConditions(partner_ids=[8], change_days_max=10),
                level="auto_notice",
                note="Hidraulica may move dates up to ten days",
            )
        ]
    )
    gateway = _gateway(ports, store, policy)
    big = ApprovalRequest(
        kind="po_change",
        summary="Move a line by 20 days",
        payload={"po_name": "P00074"},
        po_id=74,
        facts=ActionFacts(partner_id=8, partner_name="Proveedor Hidraulica", change_days=20),
        revert={"po_id": 74, "lines": []},
    )
    out = await gateway.prepare(_state(), "change", big)
    assert "pending_approvals" in out
    reasoning = ports.created[0]["payload"]["reasoning"]
    assert reasoning["facts"] == ["supplier Proveedor Hidraulica", "date move 20 day(s)"]
    assert reasoning["rule"] == "no rule matched"
    assert "allowing date moves up to 20 day(s)" in reasoning["counterfactual"]

    small = big.model_copy(
        update={
            "summary": "Move a line by 3 days",
            "facts": ActionFacts(partner_id=8, partner_name="Proveedor Hidraulica", change_days=3),
        }
    )
    out = await gateway.prepare(_state(), "change2", small)
    assert "approvals" in out  # ran alone under the rule
    [row] = store.rows
    feed = row.payload["reasoning"]
    assert feed["rule"] == "rule trusted-dates: Hidraulica may move dates up to ten days"
    assert feed["counterfactual"].startswith("It ran alone under a rule")
