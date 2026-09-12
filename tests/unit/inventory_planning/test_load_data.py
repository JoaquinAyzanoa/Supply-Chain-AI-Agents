"""``load_data`` joins the reads into one dataset per product."""

from __future__ import annotations

from datetime import date, timedelta

from inventory_planning.models import PlanningDataset
from inventory_planning.nodes.load_data import load_dataset
from inventory_planning.testing import demo_ports, product
from sc_core.odoo.models import DailyDemand

AS_OF = date(2026, 9, 14)


async def test_dataset_has_one_entry_per_product_with_everything_joined() -> None:
    ports = demo_ports(as_of=AS_OF)
    ds = await load_dataset(
        ports, as_of=AS_OF, history_days=730, category="Hidráulica", warehouse_code="WH"
    )
    assert ds.warehouse_code == "WH" and ds.as_of == AS_OF
    assert [p.ref for p in ds.products] == ["CBEA-LHN", "CXDA-XCN", "LODC-XDN", "990-011-007"]
    cbea = ds.product(1)
    assert cbea is not None
    assert cbea.on_hand == 20 and cbea.reserved == 2 and cbea.incoming_qty == 10
    assert cbea.preferred_supplier is not None and cbea.preferred_supplier.delay_days == 30
    assert [t.partner_id for t in cbea.suppliers] == [20, 21]
    assert cbea.orderpoint is not None and cbea.orderpoint.product_min_qty == 4
    lodc = ds.product(3)
    assert lodc is not None and lodc.suppliers == [] and lodc.orderpoint is None
    assert lodc.on_hand == 3
    # demand is sorted, inside the window, and re-bucketed by week on request
    assert cbea.demand == sorted(cbea.demand, key=lambda r: r.day)
    assert all(ds.history_start <= r.day < AS_OF for r in cbea.demand)
    weekly = cbea.weekly_demand(ds.history_start, AS_OF - timedelta(days=1))
    assert len(weekly) == 104 and sum(weekly) == sum(r.ordered for r in cbea.demand)
    assert [c[0] for c in ports.calls] == [
        "warehouse",
        "products",
        "daily_sales",
        "on_hand",
        "open_supply",
        "supplier_terms",
        "orderpoints",
    ]
    assert ports.calls[1] == ("products", "Hidráulica")


async def test_product_filter_and_missing_data_default_to_zero() -> None:
    ports = demo_ports(as_of=AS_OF)
    ports.products_.append(product(9, "NEW-001", "Nuevo sin historia"))
    ds = await load_dataset(ports, as_of=AS_OF, history_days=365, product_ids=[9, 3])
    assert [p.product_id for p in ds.products] == [3, 9]
    new = ds.product(9)
    assert new is not None and new.demand == [] and new.on_hand == 0 and new.incoming == []
    assert new.weekly_demand(ds.history_start, AS_OF) == [0.0] * 53
    assert ds.history_days == 364  # whole weeks ending yesterday


def test_history_window_is_whole_weeks_ending_yesterday() -> None:
    start, end = PlanningDataset.history_window(date(2026, 9, 14), 730)
    assert end == date(2026, 9, 13) and (end - start).days + 1 == 104 * 7


def test_today_is_excluded_from_demand() -> None:
    ports = demo_ports(as_of=AS_OF)
    ports.demand.append(DailyDemand(product_id=1, day=AS_OF, ordered=99, delivered=0))
    import asyncio

    ds = asyncio.run(load_dataset(ports, as_of=AS_OF, history_days=730))
    cbea = ds.product(1)
    assert cbea is not None and all(r.day != AS_OF for r in cbea.demand)
