"""The sourcing graph on fakes: a round from invitation to award, a counter-offer within
the cap, an alternative source for a late order."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sc_core.graph import cleared
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import SourcingTask
from sourcing.graph.nodes.common import Limits
from sourcing.graph.nodes.compare import RecommendationText
from sourcing.graph.nodes.negotiate import JustificationText
from sourcing.infra.store import is_due
from sourcing.testing import (
    ALTERNA,
    HIDRAULICA,
    IMPORTADORA,
    VALVE,
    FakeSourcingPorts,
    supplier_reply,
)
from tests.unit.graph.toy import FakeApprovalPorts

from .conftest import NOW


def _decision(approval_id: int, status: str = "approved", **details: Any) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "status": status,
        "resolved_by": "Ana",
        "reason": None,
        "details": details or None,
    }


async def test_a_round_invites_the_ranked_suppliers_and_compares_to_an_award(
    make_agent: Any,
    ports: FakeSourcingPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    agent = make_agent()
    # the round starts from the planner's RFQ to Hidraulica: the two others are invited
    ports.supplier_replies.append(supplier_reply("send_rfq", "round_1_rfq9", "sent", "RFQ sent"))
    result = await agent.run(SourcingTask(kind="quote_round", case_id="round_1", po_name="P00081"))
    assert result.status == "sent" and result.round_id == 1
    assert [i.partner_id for i in result.invited] == [ALTERNA, IMPORTADORA]
    assert [i.status for i in result.invited] == ["sent", "no_email"]
    assert "2 supplier(s) invited" in result.outcome.summary
    # two RFQs created for the invitees, grouped as alternatives with the source RFQ
    assert [r["partner_id"] for r in ports.created_rfqs] == [ALTERNA, IMPORTADORA]
    assert (
        ports.created_rfqs[0]["external_ref"] == "round-1-9"
        and "P00081" in ports.created_rfqs[0]["origin"]
    )
    assert ports.groups == [[81, 901, 902]]
    [sent] = ports.sent_tasks
    assert sent["kind"] == "send_rfq" and sent["po_name"] == "P00901"
    assert sent["case_id"] == "round_1_rfq9" and sent["sent_for"] == "round_1"
    round_ = await ports.get_round(1)
    assert (
        round_ is not None
        and round_.status == "open"
        and round_.deadline == NOW + timedelta(days=5)
    )
    assert [(r.partner_id, r.status) for r in round_.rfqs] == [
        (HIDRAULICA, "sent"),
        (ALTERNA, "sent"),
        (IMPORTADORA, "no_email"),
    ]
    assert not is_due(round_, NOW + timedelta(days=1))

    # Hidraulica answers on the source RFQ (its line carries the quoted price), Alterna too;
    # the importer stays silent and is compared on its list price
    ports.rfqs[81] = ports.rfqs[901].model_copy(
        update={
            "po_id": 81,
            "po_name": "P00081",
            "partner_id": HIDRAULICA,
            "partner_name": "Proveedor Hidraulica",
        }
    )
    ports.supplier_replied(81, 102.0, lead_days=28)
    ports.supplier_replied(901, 112.0, lead_days=18)
    chat.responses.append(
        RecommendationText(
            text="Proveedor Hidraulica lands cheapest with a proven record; "
            "the importer is a list price only."
        )
    )
    # the comparison runs on its own thread (the director makes one per comparison)
    compared = await agent.run(
        SourcingTask(kind="compare_quotes", case_id="round_1_cmp1", round_id=1)
    )
    assert compared.status == "awaiting_approval" and compared.outcome.approval_id == 101
    assert compared.comparison is not None
    comparison = compared.comparison
    assert comparison.replied == 2 and comparison.invited == 3
    assert comparison.recommended_partner_id == HIDRAULICA
    by_partner = {q.partner_id: q for q in comparison.quotes}
    assert by_partner[HIDRAULICA].source == "reply" and by_partner[HIDRAULICA].total == round(
        102.0 * 1.05 * 10, 2
    )
    assert (
        by_partner[IMPORTADORA].source == "price_list"
        and "list price, no reply yet" in by_partner[IMPORTADORA].reasons
    )
    assert "proven record" in comparison.recommendation
    award = approval_ports.created[0]
    assert award["kind"] == "award" and award["payload"]["mode"] == "round"
    assert award["payload"]["recommended_partner_id"] == HIDRAULICA
    assert "recommended" in award["summary"] and "2 of 3 replied" in award["summary"]
    assert (await ports.get_round(1)).status == "awaiting_award"  # type: ignore[union-attr]
    assert (await ports.get_round(1)).award_approval_id == 101  # type: ignore[union-attr]

    # the buyer awards Alterna instead: their RFQ is confirmed, Hidraulica gets a decline,
    # the importer's unsent RFQ is just cancelled
    ports.supplier_replies.append(
        supplier_reply("decline_quote", "round_1_decline8", "sent", "declined")
    )
    done = await agent.resume("round_1_cmp1", _decision(101, partner_id=ALTERNA))
    assert done.status == "applied" and done.awarded_po_name == "P00901"
    assert (
        "Hidráulica Alterna SAC (P00901)" in done.outcome.summary
        and "1 other quote(s) declined" in done.outcome.summary
    )
    assert ports.confirmed == [901] and sorted(ports.cancelled) == [81, 902]
    # the losers are cancelled before the winner is confirmed (Odoo's alternatives wizard)
    assert ports.journal == [("cancel", 81), ("cancel", 902), ("confirm", 901)]
    decline = ports.sent_tasks[-1]
    assert decline["kind"] == "decline_quote" and decline["po_name"] == "P00081"
    assert decline["pre_approved"] and "no commitment" in decline["pre_approved"]
    final = await ports.get_round(1)
    assert final is not None and final.status == "awarded" and final.awarded_partner_id == ALTERNA
    assert [(r.partner_id, r.status) for r in final.rfqs][0] == (HIDRAULICA, "declined")
    assert (
        ports.notes
        and ports.notes[0][0] == 901
        and "awarded to Hidráulica Alterna SAC by Ana" in ports.notes[0][1]
    )
    assert cleared(await agent.snapshot("round_1_cmp1"))


async def test_a_round_is_due_when_the_deadline_passes_or_everyone_answered(
    make_agent: Any, ports: FakeSourcingPorts
) -> None:
    ports.supplier_replies.append(supplier_reply("send_rfq", "round_2_rfq9", "sent"))
    await make_agent().run(
        SourcingTask(
            kind="quote_round",
            case_id="round_2",
            po_name="P00081",
            exclude_partner_ids=[IMPORTADORA],
        )
    )
    round_ = await ports.get_round(1)
    assert round_ is not None
    assert not is_due(round_, NOW)
    assert is_due(round_, NOW + timedelta(days=5))
    await ports.update_rfq(1, HIDRAULICA, replied_at=NOW)
    await ports.update_rfq(1, ALTERNA, replied_at=NOW)
    assert is_due(await ports.get_round(1), NOW + timedelta(hours=1))  # type: ignore[arg-type]
    assert await ports.due_rounds(NOW + timedelta(hours=1)) != []


async def test_rejecting_the_award_keeps_the_round_for_a_re_run(
    make_agent: Any, ports: FakeSourcingPorts, chat: ScriptedChatClient
) -> None:
    ports.supplier_replies.append(supplier_reply("send_rfq", "round_3_rfq9", "sent"))
    agent = make_agent()
    await agent.run(SourcingTask(kind="quote_round", case_id="round_3", po_name="P00081"))
    chat.responses.append(
        RecommendationText(
            text="Only list prices so far; the importer is cheapest but slow and unproven."
        )
    )
    compared = await agent.run(
        SourcingTask(kind="compare_quotes", case_id="round_3_cmp1", po_name="P00081")
    )
    assert compared.status == "awaiting_approval" and compared.comparison is not None
    assert compared.comparison.replied == 0
    rejected = await agent.resume("round_3_cmp1", _decision(101, "rejected"))
    assert rejected.status == "rejected" and "rejected by Ana" in rejected.outcome.summary
    assert (await ports.get_round(1)).status == "rejected"  # type: ignore[union-attr]
    assert ports.confirmed == [] and ports.cancelled == []


async def test_a_counter_offer_stays_within_the_cap_and_is_sent_after_approval(
    make_agent: Any,
    ports: FakeSourcingPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    # Hidraulica quoted 110 on the RFQ; we last paid 104.16 and the importer lists 90.72
    ports.rfqs[81] = ports.rfqs.get(81) or (await _rfq(ports, 81))
    ports.supplier_replied(81, 110.0, lead_days=30)
    chat.responses.append(
        JustificationText(
            text="We ask 104.16: our last price for this valve; delivery terms unchanged."
        )
    )
    agent = make_agent(limits=Limits(cap_pct=10.0, max_rounds=2))
    result = await agent.run(
        SourcingTask(kind="counter_offer", case_id="offer_1", po_name="P00081")
    )
    assert result.status == "awaiting_approval" and result.counter_offer is not None
    offer = result.counter_offer
    assert offer.current_price == 110.0 and offer.floor_price == 99.0
    # the importer lists 90.72 but needs 20 units: not evidence for a 10-unit line
    assert offer.offered_price == 104.16 and offer.basis == "our last paid price of 104.16"
    assert offer.round_no == 1 and offer.max_rounds == 2 and "last price" in offer.justification
    created = approval_ports.created[0]
    assert created["kind"] == "negotiation_offer" and created["po_id"] == 81
    assert created["payload"]["facts"]["change_pct"] == 5.31
    assert "104.16 USD instead of 110.00 USD" in created["summary"]

    # the buyer edits the number down, within the cap
    ports.supplier_replies.append(
        supplier_reply("counter_offer", "offer_1_offer1", "sent", "offer sent")
    )
    sent = await agent.resume("offer_1", _decision(101, offered_price=100.0))
    assert (
        sent.status == "sent"
        and sent.counter_offer is not None
        and sent.counter_offer.offered_price == 100.0
    )
    task = ports.sent_tasks[-1]
    assert task["kind"] == "counter_offer" and task["po_name"] == "P00081"
    assert "we ask 100.00 USD per unit instead of the quoted 110.00" in task["notes"]
    assert task["pre_approved"] == "negotiation_offer #101 approved by Ana"
    [negotiation] = await ports.negotiations_for("P00081", VALVE)
    assert (
        negotiation.status == "sent"
        and negotiation.offered_price == 100.0
        and negotiation.round_no == 1
    )


async def test_an_edit_outside_the_cap_is_refused(
    make_agent: Any, ports: FakeSourcingPorts, chat: ScriptedChatClient
) -> None:
    ports.rfqs[81] = await _rfq(ports, 81)
    ports.supplier_replied(81, 110.0)
    chat.responses.append(JustificationText(text="Reasonable ask based on history."))
    agent = make_agent()
    await agent.run(SourcingTask(kind="counter_offer", case_id="offer_2", po_name="P00081"))
    refused = await agent.resume("offer_2", _decision(101, offered_price=80.0))
    assert refused.status == "failed" and "below the floor" in refused.outcome.summary
    assert ports.sent_tasks == []


async def test_rounds_are_counted_and_the_third_goes_to_a_person(
    make_agent: Any,
    ports: FakeSourcingPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    ports.rfqs[81] = await _rfq(ports, 81)
    ports.supplier_replied(81, 110.0)
    agent = make_agent(limits=Limits(cap_pct=10.0, max_rounds=2))
    for n in (1, 2):
        chat.responses.append(JustificationText(text=f"Round {n}: still above our last price."))
        await agent.run(SourcingTask(kind="counter_offer", case_id=f"offer_r{n}", po_name="P00081"))
        ports.supplier_replies.append(
            supplier_reply("counter_offer", f"offer_r{n}_offer{n}", "sent")
        )
        await agent.resume(f"offer_r{n}", _decision(100 + n))
    third = await agent.run(
        SourcingTask(kind="counter_offer", case_id="offer_r3", po_name="P00081")
    )
    assert (
        third.status == "escalated"
        and "2 negotiation round(s) already made" in third.outcome.summary
    )
    assert approval_ports.created[-1]["kind"] == "escalation" and third.outcome.approval_id == 103


async def test_nothing_to_negotiate_when_the_quote_is_at_target(
    make_agent: Any, ports: FakeSourcingPorts, chat: ScriptedChatClient
) -> None:
    ports.rfqs[81] = await _rfq(ports, 81)
    ports.supplier_replied(81, 90.0)  # under everything we know
    ports.entries = []
    result = await make_agent().run(
        SourcingTask(kind="counter_offer", case_id="offer_3", po_name="P00081")
    )
    assert (
        result.status == "no_action" and "already at or under the target" in result.outcome.summary
    )
    assert chat.calls == []


async def test_alternate_source_proposes_a_direct_order_from_the_price_list(
    make_agent: Any,
    ports: FakeSourcingPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    chat.responses.append(
        RecommendationText(
            text="Alterna delivers in 18 days at a list price; the importer needs 20 units minimum."
        )
    )
    agent = make_agent()
    result = await agent.run(
        SourcingTask(
            kind="alternate_source", case_id="alt_1", po_name="P00077", partner_id=HIDRAULICA
        )
    )
    assert result.status == "awaiting_approval" and result.comparison is not None
    partners = {q.partner_id for q in result.comparison.quotes}
    assert partners == {ALTERNA, IMPORTADORA}  # never the late supplier itself
    assert all(q.source == "price_list" for q in result.comparison.quotes)
    award = approval_ports.created[0]
    assert award["kind"] == "award" and award["payload"]["mode"] == "direct"
    assert award["summary"].startswith("Alternative source for P00077")
    # approved: an RFQ is created for the chosen alternate and confirmed
    done = await agent.resume("alt_1", _decision(101, partner_id=ALTERNA))
    assert done.status == "applied" and done.awarded_po_name == "P00901"
    assert (
        ports.created_rfqs[0]["partner_id"] == ALTERNA
        and ports.created_rfqs[0]["external_ref"] == "alt-1-9"
    )
    assert ports.confirmed == [901] and ports.sent_tasks == []
    assert (await ports.get_round(1)).status == "awarded"  # type: ignore[union-attr]


async def test_alternate_source_without_list_prices_starts_a_round(
    make_agent: Any, ports: FakeSourcingPorts
) -> None:
    ports.entries = []  # nobody lists the valve
    ports.supplier_replies.append(
        supplier_reply("send_rfq", "alt_2_rfq9", "awaiting_approval", "draft")
    )
    result = await make_agent().run(
        SourcingTask(kind="alternate_source", case_id="alt_2", po_name="P00077")
    )
    assert result.status == "awaiting_approval" and result.round_id == 1
    assert [i.partner_id for i in result.invited] == [ALTERNA, IMPORTADORA]
    assert result.invited[0].status == "awaiting_approval"


async def test_alternate_source_with_nobody_escalates(
    make_agent: Any, ports: FakeSourcingPorts, approval_ports: FakeApprovalPorts
) -> None:
    ports.options = [o for o in ports.options if o.partner_id == HIDRAULICA]
    ports.entries = []
    result = await make_agent().run(
        SourcingTask(kind="alternate_source", case_id="alt_3", po_name="P00077")
    )
    assert result.status == "escalated" and approval_ports.created[0]["kind"] == "escalation"
    assert "no alternative supplier lists the products of P00077" in result.outcome.summary


async def test_a_round_without_anyone_to_invite_does_nothing(
    make_agent: Any, ports: FakeSourcingPorts
) -> None:
    ports.options = [o for o in ports.options if o.partner_id == HIDRAULICA]
    result = await make_agent().run(
        SourcingTask(kind="quote_round", case_id="round_0", po_name="P00081")
    )
    assert result.status == "no_action" and "no supplier to invite" in result.outcome.summary
    assert ports.created_rfqs == [] and await ports.rounds() == []


async def test_a_broken_supplier_agent_marks_the_invitation_failed(
    make_agent: Any, ports: FakeSourcingPorts
) -> None:
    from sourcing.testing import BrokenSupplierAgent

    ports.supplier_replies.append(BrokenSupplierAgent("supplier agent down"))
    result = await make_agent().run(
        SourcingTask(
            kind="quote_round",
            case_id="round_9",
            po_name="P00081",
            exclude_partner_ids=[IMPORTADORA],
        )
    )
    # the incumbent's own RFQ is the one live invitation; the new one failed
    assert result.status == "sent" and result.invited[0].status == "failed"
    assert (await ports.get_round(1)).rfqs[1].status == "failed"  # type: ignore[union-attr]


async def _rfq(ports: FakeSourcingPorts, po_id: int) -> Any:
    from sc_core.schema.a2a import QuoteLine
    from sourcing.domain.models import RfqSnapshot

    order = next(o for o in ports.orders.values() if o.po_id == po_id)
    basket = ports.baskets[order.po_name]
    return RfqSnapshot(
        po_id=po_id,
        po_name=order.po_name,
        partner_id=order.partner_id,
        partner_name=order.partner_name,
        state=order.state,
        currency="USD",
        lines=[QuoteLine(product_id=b.product_id, product=b.product, qty=b.qty) for b in basket],
    )
