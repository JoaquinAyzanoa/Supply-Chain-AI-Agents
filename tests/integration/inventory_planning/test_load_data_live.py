"""The planning dataset from the seeded demo Odoo, in well under a minute."""

from __future__ import annotations

import time

import pytest

from inventory_planning.nodes.load_data import load_dataset
from inventory_planning.ports import LiveDataPorts
from sc_core.odoo.client import OdooClient
from sc_core.shared.time import local_today

pytestmark = [pytest.mark.integration, pytest.mark.odoo]


async def test_demo_dataset_loads_every_sun_product_quickly(odoo: OdooClient) -> None:
    started = time.perf_counter()
    ds = await load_dataset(
        LiveDataPorts(odoo),
        as_of=local_today(),
        history_days=730,
        category="Hidráulica",
    )
    seconds = time.perf_counter() - started
    refs = {p.ref for p in ds.products}
    if "CBEA-LHN" not in refs:
        pytest.skip("demo catalogue missing; run `just odoo-seed`")
    assert seconds < 60, f"load took {seconds:.1f}s"
    assert len(ds.products) == 14 and ds.warehouse_code == "WH"
    cbea = ds.product(next(p.product_id for p in ds.products if p.ref == "CBEA-LHN"))
    assert cbea is not None
    assert len(cbea.demand) > 80, "two years of weekly orders expected"
    assert cbea.on_hand > 0 and cbea.suppliers and cbea.orderpoint is not None
    assert [t.partner_name for t in cbea.suppliers][0] == "Proveedor Hidraulica"
    weekly = cbea.weekly_demand(ds.history_start, ds.as_of)
    assert 4 < sum(weekly) / len(weekly) < 12, "weekly mean around 7 in the seed profile"
    no_rule = [p for p in ds.products if p.orderpoint is None]
    assert [p.ref for p in no_rule] == ["LODC-XDN"], "the seed's deliberate missing rule"
    rows = sum(len(p.demand) for p in ds.products)
    print(f"\n>>> {len(ds.products)} products, {rows} demand rows in {seconds:.1f}s")
