"""An approved weekly scorecard run changes the planner's lead time (P9-S4-T5).

The performance agent's ``apply`` writes the measured lead time into
``planning_params`` (a real Postgres here) and the promise on the supplier's
price list (a recording Odoo stub). The next daily plan, run by the real
planner graph on the demo catalogue, picks the measured value up.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, cast

import pytest
from pydantic import BaseModel

from inventory_planning.agent import InventoryPlanningAgent
from inventory_planning.graph import Deps, build_graph
from inventory_planning.nodes.explain import Explanation
from inventory_planning.policy import PostgresParamsStore, ProductParams
from inventory_planning.runs import MemoryRunStore
from inventory_planning.testing import PRIMARY, FakePublisher, FakeWritePorts, demo_ports
from sc_core.graph import ApprovalGateway, memory_checkpointer
from sc_core.infra.db import Database
from sc_core.infra.settings import LangfuseCfg, PlanningCfg
from sc_core.llm.client import ChatResult
from sc_core.llm.testing import ScriptedChatClient
from sc_core.odoo.models import Ref, SupplierInfo
from sc_core.schema.a2a import InventoryPlanningTask, SupplierScore
from sc_core.schema.planning import ReplenishmentLine
from supplier_performance.ports import LivePerformancePorts
from supplier_performance.testing import price_entry
from tests.unit.graph.toy import FakeApprovalPorts

pytestmark = pytest.mark.integration

AS_OF = date(2026, 9, 14)
PRODUCT_ID = 1  # CBEA-LHN in the demo catalogue, bought from Proveedor Hidraulica (30 d promised)


class RecordingOdoo:
    """Only what ``apply`` needs from Odoo: ``write``; everything is recorded."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, list[int], dict[str, Any]]] = []

    async def write(self, model: str, ids: Sequence[int], values: dict[str, Any]) -> bool:
        self.writes.append((model, list(ids), dict(values)))
        return True


class PriceList:
    """The supplier's price-list entries, as ``SupplierInfoRepo`` would read them."""

    def __init__(self, entries: list[SupplierInfo]) -> None:
        self.entries = entries

    async def for_partner(self, partner_id: int) -> list[SupplierInfo]:
        return [e for e in self.entries if e.partner_id.id == partner_id]

    async def for_product(self, product_id: int) -> list[SupplierInfo]:
        return [e for e in self.entries if e.product_id and e.product_id.id == product_id]


class AnyChat(ScriptedChatClient):
    """Answers every structured call with an explanation and every text call with a
    summary, so the run's number of exceptions does not matter to the test."""

    async def complete(self, messages: Sequence[Any], **kwargs: Any) -> ChatResult:
        fmt = kwargs.get("response_format")
        if isinstance(fmt, type) and issubclass(fmt, BaseModel):
            self.responses.append(
                Explanation(
                    product="?",
                    headline="Atención.",
                    reasoning="Los números lo justifican.",
                    recommended_action="Aprobar.",
                )
            )
        else:
            self.responses.append("Resumen de la corrida diaria.")
        return await super().complete(messages, **kwargs)


def planner(db: Database) -> InventoryPlanningAgent:
    gateway = ApprovalGateway(
        FakeApprovalPorts(),
        agent_name="inventory_planning",
        callback_url="http://inventory_planning:8000/approvals/callback",
        callback_secret="s",
        approver_user_id=2,
        deadline_days=2,
    )
    deps = Deps(
        data=demo_ports(as_of=AS_OF),
        writes=FakeWritePorts(),
        params=PostgresParamsStore(db),
        runs=MemoryRunStore(),
        chat=AnyChat(),
        approvals=gateway,
        cfg=PlanningCfg(product_category="", history_days=730),
        publish=FakePublisher().publish,
        langfuse=LangfuseCfg(enabled=False),
        today=lambda: AS_OF,
    )
    return InventoryPlanningAgent(
        build_graph(deps, memory_checkpointer()), model="fake-model", writes=deps.writes
    )


async def plan_line(db: Database, case_id: str) -> ReplenishmentLine:
    result = await planner(db).run(InventoryPlanningTask(kind="daily_plan", case_id=case_id))
    assert result.status == "awaiting_approval" and result.proposal is not None
    return next(ln for ln in result.proposal.lines if ln.product_id == PRODUCT_ID)


async def test_measured_lead_time_reaches_the_next_daily_plan(db: Database) -> None:
    params = PostgresParamsStore(db)
    await params.save(ProductParams.default_for(PRODUCT_ID, "A"))

    before = await plan_line(db, "plan_before")
    assert before.lead_time_days == 30.0  # the supplier's promise, nothing measured yet
    assert before.supplier_id == PRIMARY.id

    odoo = RecordingOdoo()
    entry = price_entry(PRIMARY.id, PRIMARY.name, price=104.0, delay=30).model_copy(
        update={"product_id": Ref(id=PRODUCT_ID, name="[CBEA-LHN] Contrabalance 3:1 T-11A")}
    )
    ports = LivePerformancePorts(
        odoo=cast(Any, odoo),
        db=db,
        purchase_orders=cast(Any, None),
        move_lines=cast(Any, None),
        mail_links=cast(Any, None),
        supplier_info=cast(Any, PriceList([entry])),
        agent_runs=cast(Any, None),
    )
    score = SupplierScore(
        partner_id=PRIMARY.id,
        partner_name=PRIMARY.name,
        period_start=date(2025, 9, 13),
        period_end=date(2026, 9, 13),
        otif=0.5,
        lead_time_mean_days=42.28,
        lead_time_sigma_days=3.43,
        score=75.55,
        samples={"lines": 140, "orders": 10},
    )
    await ports.save_run("run_w1", [score])
    applied = await ports.apply("run_w1", [score], approval_id=30)
    assert applied == {"partners": 1, "price_list_entries": 1, "planning_params": 1}
    assert ("res.partner", [PRIMARY.id], odoo.writes[0][2]) == odoo.writes[0]
    assert (
        odoo.writes[0][2]["sc_score"] == 75.55 and odoo.writes[0][2]["sc_lead_time_mean"] == 42.28
    )
    assert odoo.writes[1] == ("product.supplierinfo", [entry.id], {"delay": 42})

    stored = (await params.for_products([PRODUCT_ID]))[PRODUCT_ID]
    assert stored.lead_time_mean_days == 42.28 and stored.source == "measured"

    after = await plan_line(db, "plan_after")
    assert after.lead_time_days == 42.28 and after.sigma_lead_time_days == 3.43
    assert after.rop > before.rop  # a longer lead time means reordering earlier
    assert (await db.fetch_one("SELECT applied_at FROM supplier_scores WHERE run_id = 'run_w1'"))[
        "applied_at"
    ] is not None
