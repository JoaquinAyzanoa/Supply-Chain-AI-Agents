"""A round from the planner's needs: suppliers asked for what they list, award per line."""

from __future__ import annotations

from datetime import date
from typing import Any

from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import Need, SourcingTask
from sourcing.models import PriceEntry
from sourcing.nodes.compare import RecommendationText
from sourcing.nodes.round import pick_invitees
from sourcing.testing import (
    ALTERNA,
    HIDRAULICA,
    IMPORTADORA,
    VALVE,
    FakeSourcingPorts,
    demo_options,
    supplier_reply,
)
from tests.unit.graph.toy import FakeApprovalPorts

HOSE = 2


def _decision(approval_id: int, **details: Any) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "status": "approved",
        "resolved_by": "Ana",
        "reason": None,
        "details": details or None,
    }


def test_invitees_are_asked_only_for_what_they_list() -> None:
    options = demo_options()
    options[0] = options[0].model_copy(update={"product_ids": [VALVE, HOSE]})
    options[1] = options[1].model_copy(update={"product_ids": [VALVE]})
    options[2] = options[2].model_copy(update={"product_ids": [HOSE]})
    asked = pick_invitees(options, basket_products=[VALVE, HOSE], named=[], excluded=set(), top_n=2)
    assert asked == {HIDRAULICA: [VALVE, HOSE], ALTERNA: [VALVE], IMPORTADORA: [HOSE]}
    # a named partner who lists nothing yet is asked for the whole basket
    asked = pick_invitees(
        options, basket_products=[VALVE, HOSE], named=[77], excluded=set(), top_n=1
    )
    assert (
        asked[77] == [VALVE, HOSE] and asked[HIDRAULICA] == [VALVE, HOSE] and ALTERNA not in asked
    )


async def test_a_round_from_needs_asks_each_supplier_for_its_products_and_awards_per_line(
    make_agent: Any,
    ports: FakeSourcingPorts,
    chat: ScriptedChatClient,
    approval_ports: FakeApprovalPorts,
) -> None:
    ports.products[HOSE] = "[MH-R2-08] Manguera"
    ports.entries = [
        PriceEntry(
            partner_id=HIDRAULICA, product_id=VALVE, price=104.16, currency="USD", lead_days=30
        ),
        PriceEntry(
            partner_id=HIDRAULICA, product_id=HOSE, price=12.0, currency="USD", lead_days=30
        ),
        PriceEntry(
            partner_id=ALTERNA, product_id=VALVE, price=114.24, currency="USD", lead_days=18
        ),
        PriceEntry(
            partner_id=IMPORTADORA,
            product_id=HOSE,
            price=9.5,
            currency="USD",
            lead_days=60,
            min_qty=20,
        ),
    ]
    ports.options = [o.model_copy(update={"has_email": True}) for o in demo_options()]
    ports.supplier_replies.extend(
        [
            supplier_reply("send_rfq", "round_plan_run_1_rfq8", "sent"),
            supplier_reply("send_rfq", "round_plan_run_1_rfq9", "sent"),
            supplier_reply("send_rfq", "round_plan_run_1_rfq10", "sent"),
        ]
    )
    agent = make_agent()
    needs = [
        Need(
            product_id=VALVE,
            product="[CBEA-LHN] Válvula",
            qty=10,
            expected_price=104.16,
            need_date=date(2026, 10, 14),
        ),
        Need(
            product_id=HOSE,
            product="[MH-R2-08] Manguera",
            qty=100,
            expected_price=12.0,
            need_date=date(2026, 10, 14),
        ),
    ]
    started = await agent.run(
        SourcingTask(
            kind="quote_round", case_id="round_plan_run_1", needs=needs, reason="daily plan run_1"
        )
    )
    assert started.status == "sent" and started.round_id == 1
    # one RFQ per supplier, each with the products that supplier lists, dated by the need
    made = {r["partner_id"]: r for r in ports.created_rfqs}
    assert [ln["product_id"] for ln in made[HIDRAULICA]["lines"]] == [VALVE, HOSE]
    assert [ln["product_id"] for ln in made[ALTERNA]["lines"]] == [VALVE]
    assert [ln["product_id"] for ln in made[IMPORTADORA]["lines"]] == [HOSE]
    assert made[HIDRAULICA]["lines"][0]["need_date"] == "2026-10-14"
    round_ = await ports.get_round(1)
    assert round_ is not None and [(r.partner_id, r.product_ids) for r in round_.rfqs] == [
        (HIDRAULICA, [VALVE, HOSE]),
        (ALTERNA, [VALVE]),
        (IMPORTADORA, [HOSE]),
    ]
    assert len(ports.sent_tasks) == 3 and started.invited[0].status == "sent"

    # everyone answers: Alterna is cheapest on the valve, Hidraulica on the hose
    hid, alt, imp = (made[p]["po_id"] for p in (HIDRAULICA, ALTERNA, IMPORTADORA))
    ports.supplier_replied(hid, 105.0, lead_days=30)
    ports.rfqs[hid] = ports.rfqs[hid].model_copy(
        update={
            "lines": [
                ln.model_copy(update={"price_unit": 105.0 if ln.product_id == VALVE else 11.0})
                for ln in ports.rfqs[hid].lines
            ]
        }
    )
    ports.supplier_replied(alt, 99.0, lead_days=18)
    ports.supplier_replied(imp, 12.5, lead_days=60)
    chat.responses.append(
        RecommendationText(
            text="Alterna wins the valve on price, Hidraulica the hose; the importer is slow."
        )
    )
    compared = await agent.run(
        SourcingTask(kind="compare_quotes", case_id="round_plan_run_1_cmp1", round_id=1)
    )
    assert compared.status == "awaiting_approval" and compared.comparison is not None
    awards = {a.product_id: a.partner_id for a in compared.comparison.line_awards}
    assert awards == {VALVE: ALTERNA, HOSE: HIDRAULICA}

    # the person keeps the split: Alterna's RFQ is confirmed whole, Hidraulica's loses the
    # valve line and is confirmed, the importer is declined and cancelled
    ports.supplier_replies.append(
        supplier_reply("decline_quote", "round_plan_run_1_cmp1_decline10", "sent")
    )
    done = await agent.resume(
        "round_plan_run_1_cmp1", _decision(101, lines={str(VALVE): ALTERNA, str(HOSE): HIDRAULICA})
    )
    assert done.status == "applied"
    assert sorted(done.awarded_po_names) == sorted(
        [made[HIDRAULICA]["po_name"], made[ALTERNA]["po_name"]]
    )
    assert ports.dropped == [(hid, [HOSE])]
    assert ports.cancelled == [imp] and sorted(ports.confirmed) == sorted([hid, alt])
    assert ports.journal[0] == ("cancel", imp)  # losers first, then the winners
    assert (
        ports.sent_tasks[-1]["kind"] == "decline_quote"
        and ports.sent_tasks[-1]["po_name"] == made[IMPORTADORA]["po_name"]
    )
    final = await ports.get_round(1)
    assert (
        final is not None
        and final.status == "awarded"
        and "Hidráulica Alterna SAC" in done.outcome.summary
    )
