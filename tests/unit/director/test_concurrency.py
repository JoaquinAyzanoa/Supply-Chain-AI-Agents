"""One run per order at a time, deferred events replayed, missed confirmations reconciled."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import Any

from director.testing import MemoryDirectorModule
from sc_core.a2a import AgentReply
from sc_core.odoo.models import PurchaseOrder, Ref
from sc_core.schema.events import event_id_for

from .helpers import agent_reply, linked


class SlowCaller:
    """Records how many runs overlap; each call takes a little while."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.order: list[str] = []

    async def send(self, task_json: str, *, case_id: str, metadata: Any = None) -> AgentReply:
        thread = json.loads(task_json)["case_id"]
        self.order.append(f"start {thread}")
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        self.order.append(f"end {thread}")
        return agent_reply("handle_inbound", thread, "no_action", "ok")


async def test_two_events_on_the_same_order_run_one_after_the_other() -> None:
    caller = SlowCaller()
    module = MemoryDirectorModule(supplier_comms=caller)
    a = linked(case_id="case_a", graph_message_id="A", po_name="P00015")
    b = linked(case_id="case_b", graph_message_id="B", po_name="P00015")
    await asyncio.gather(module.orchestrator.handle(a), module.orchestrator.handle(b))
    assert caller.peak == 1
    assert caller.order in (
        ["start case_a", "end case_a", "start case_b", "end case_b"],
        ["start case_b", "end case_b", "start case_a", "end case_a"],
    )
    assert module.lock.acquired.count("po:P00015") == 2


async def test_events_on_different_orders_run_together() -> None:
    caller = SlowCaller()
    module = MemoryDirectorModule(supplier_comms=caller)
    events = [
        linked(case_id=f"case_{i}", graph_message_id=str(i), po_name=f"P{i:05d}") for i in range(3)
    ]
    await asyncio.gather(*(module.orchestrator.handle(e) for e in events))
    assert caller.peak == 3


async def test_locked_order_defers_the_event_and_the_replay_handles_it() -> None:
    caller = SlowCaller()
    module = MemoryDirectorModule(supplier_comms=caller, lock_wait_seconds=0.05)
    event = linked(case_id="case_a", graph_message_id="A", po_name="P00015")
    await module.inbox.store(event)
    module.lock.held.add("po:P00015")  # a run elsewhere holds the order
    result = await module.orchestrator.handle(event)
    assert result["status"] == "deferred" and caller.order == []
    assert module.inbox.deferred == {event.event_id: result["reason"]}
    assert event.event_id not in module.results.results

    module.lock.held.discard("po:P00015")
    replay = await module.orchestrator.replay_unhandled()
    assert replay["replayed"] == 1 and replay["events"] == {event.event_id: "handled"}
    assert caller.order == ["start case_a", "end case_a"]
    assert module.inbox.handled == {event.event_id} and module.inbox.deferred == {}
    assert await module.orchestrator.replay_unhandled() == {"replayed": 0, "events": {}}


class FakeConfirmed:
    def __init__(self, orders: list[PurchaseOrder]) -> None:
        self.orders = orders
        self.asked: list[date] = []

    async def confirmed_since(self, since: date) -> list[PurchaseOrder]:
        self.asked.append(since)
        return [o for o in self.orders if o.date_approve and o.date_approve.date() >= since]


def _confirmed(po_id: int, name: str, approved: date) -> PurchaseOrder:
    return PurchaseOrder(
        id=po_id,
        name=name,
        state="purchase",
        partner_id=Ref(id=9, name="Proveedor"),
        date_approve=datetime.combine(approved, datetime.min.time(), tzinfo=UTC),
        currency_id=Ref(id=1, name="PEN"),
        amount_total=100.0,
        order_line=[1, 2],
    )


async def test_reconcile_opens_cases_for_missed_confirmations_only() -> None:
    caller = SlowCaller(delay=0)
    orders = FakeConfirmed(
        [
            _confirmed(70, "P00070", date(2026, 9, 12)),  # event arrived
            _confirmed(71, "P00071", date(2026, 9, 12)),  # missed
            _confirmed(60, "P00060", date(2026, 8, 1)),  # too old to matter
        ]
    )
    module = MemoryDirectorModule(supplier_comms=caller, orders=orders)
    arrived_id = event_id_for("odoo.purchase_confirmed", 70, "purchase")
    from tests.unit.director.helpers import po_confirmed

    await module.inbox.store(po_confirmed(event_id=arrived_id, po_id=70, po_name="P00070"))

    result = await module.orchestrator.reconcile(today=date(2026, 9, 14), since_days=3)
    assert orders.asked == [date(2026, 9, 11)]
    assert result == {"checked": 2, "opened": ["P00071"]}
    assert caller.order == ["start odoo_po_71_purchase", "end odoo_po_71_purchase"]
    sent = list(module.cases.cases.values())
    assert [c.po_name for c in sent] == ["P00071"] and sent[0].kind == "eta"
    assert event_id_for("odoo.purchase_confirmed", 71, "purchase") in module.inbox.events
    promise = [e for e in module.cases.case_events if e.kind == "promise"]
    assert promise[0].payload["line_count"] == 2 and promise[0].payload["currency"] == "PEN"

    again = await module.orchestrator.reconcile(today=date(2026, 9, 14), since_days=3)
    assert again == {"checked": 2, "opened": []}
