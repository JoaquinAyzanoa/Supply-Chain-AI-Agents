"""Single-product reviews with text context, and simulations that never write."""

from __future__ import annotations

from typing import Any

from inventory_planning.nodes.explain import Explanation
from inventory_planning.nodes.review import ReviewDecision
from inventory_planning.ports import ReadOnlyWritePorts
from inventory_planning.testing import FakeDataPorts, FakeWritePorts, product
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import InventoryPlanningTask, PlanningOverrides
from tests.unit.graph.toy import FakeApprovalPorts


async def test_discontinued_note_puts_the_product_on_hold(
    make_agent: Any,
    data: FakeDataPorts,
    chat: ScriptedChatClient,
    writes: FakeWritePorts,
    approval_ports: FakeApprovalPorts,
) -> None:
    data.products_[0] = product(
        1,
        "CBEA-LHN",
        "Contrabalance 3:1 T-11A",
        description="Producto descontinuado por Sun; reemplazado por CBEA-LHN-V",
    )
    chat.responses.append(
        ReviewDecision(
            action="hold", reason="Las notas indican que el producto está descontinuado."
        )
    )
    chat.responses.append("Resumen: un producto revisado, en espera.")
    agent = make_agent()
    result = await agent.run(
        InventoryPlanningTask(
            kind="review_product",
            case_id="review_1",
            product_ids=[1],
            context="regla de reposición disparada",
        )
    )
    assert result.status == "no_action" and "on hold: CBEA-LHN" in result.outcome.summary
    assert result.proposal is not None and len(result.proposal.lines) == 1
    line = result.proposal.lines[0]
    assert line.action == "hold" and line.order_qty == 0.0
    assert line.explanation is not None and "descontinuado" in line.explanation
    assert approval_ports.created == [] and writes.rfqs == {} and writes.orderpoints == []
    facts = chat.calls[0].messages[1]["contents"][0]["text"]
    assert "descontinuado" in facts and "regla de reposición disparada" in facts
    assert "alternate suppliers: Distribuidor Alterno" in facts


async def test_switch_supplier_recomputes_with_the_alternate(
    make_agent: Any, chat: ScriptedChatClient
) -> None:
    chat.responses.append(
        ReviewDecision(
            action="switch_supplier", reason="El proveedor preferido descontinuó la pieza."
        )
    )
    chat.responses.append(
        Explanation(product="CBEA-LHN", headline="h", reasoning="r", recommended_action="a")
    )
    chat.responses.append("Resumen.")
    agent = make_agent()
    result = await agent.run(
        InventoryPlanningTask(kind="review_product", case_id="review_2", product_ids=[1])
    )
    assert result.proposal is not None
    line = result.proposal.lines[0]
    assert line.supplier_id == 21 and line.lead_time_days == 18 and line.unit_price == 114.0
    assert line.explanation is not None and line.explanation.startswith("Review:")
    assert result.status == "awaiting_approval"  # an order from the alternate still needs approval


async def test_keep_leaves_the_line_untouched(make_agent: Any, chat: ScriptedChatClient) -> None:
    chat.responses.append(
        ReviewDecision(action="keep", reason="Nada en el contexto lo contradice.")
    )
    chat.responses.append(
        Explanation(product="CBEA-LHN", headline="h", reasoning="r", recommended_action="a")
    )
    chat.responses.append("Resumen.")
    result = await make_agent().run(
        InventoryPlanningTask(kind="review_product", case_id="review_3", product_ids=[1])
    )
    assert result.proposal is not None and result.proposal.lines[0].supplier_id == 20
    assert result.proposal.lines[0].exception == "stockout_risk"


async def test_what_if_changes_numbers_and_writes_nothing(
    make_agent: Any,
    chat: ScriptedChatClient,
    writes: FakeWritePorts,
    approval_ports: FakeApprovalPorts,
) -> None:
    chat.responses.extend(
        [
            Explanation(product="CBEA-LHN", headline="h", reasoning="r", recommended_action="a"),
            "Resumen de la simulación.",
            Explanation(product="CBEA-LHN", headline="h", reasoning="r", recommended_action="a"),
            "Resumen de la simulación.",
        ]
    )
    agent = make_agent(writes=ReadOnlyWritePorts(writes))
    base = await agent.run(InventoryPlanningTask(kind="what_if", case_id="sim_1", product_ids=[1]))
    higher = await agent.run(
        InventoryPlanningTask(
            kind="what_if",
            case_id="sim_2",
            product_ids=[1],
            overrides=PlanningOverrides(service_level=0.99, lead_time_days=45),
        )
    )
    assert base.status == "no_action" and higher.status == "no_action"
    assert base.proposal is not None and higher.proposal is not None
    a, b = base.proposal.lines[0], higher.proposal.lines[0]
    assert b.service_level == 0.99 and b.lead_time_days == 45 and b.ss > a.ss and b.rop > a.rop
    assert base.outcome.summary.startswith("simulation:")
    assert approval_ports.created == [] and writes.rfqs == {} and writes.orderpoints == []
    assert writes.runs["run_" + base.run_id[4:]]["status"] == "no_action"
