"""In-memory doubles for the planner: a small catalogue with generated demand."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sc_core.odoo.models import (
    DailyDemand,
    IncomingLine,
    NewOrderLine,
    OnHand,
    Orderpoint,
    Product,
    Ref,
    SupplierTerms,
    Warehouse,
)

WAREHOUSE = Warehouse(
    id=1, name="Hidráulica Andina", code="WH", lot_stock_id=Ref(id=8, name="WH/Stock")
)
PRIMARY = Ref(id=20, name="Proveedor Hidraulica")
ALTERNATE = Ref(id=21, name="Distribuidor Alterno")


def product(product_id: int, ref: str, name: str, **overrides: Any) -> Product:
    base: dict[str, Any] = {
        "id": product_id,
        "name": name,
        "default_code": ref,
        "product_tmpl_id": Ref(id=100 + product_id, name=name),
        "categ_id": Ref(id=5, name="Hidráulica / Cartuchos"),
        "seller_ids": [1],
        "list_price": 100.0,
        "standard_price": 62.0,
        "purchase_ok": True,
        "is_storable": True,
    }
    return Product(**{**base, **overrides})


def weekly_series(
    *, weeks: int, mean: float, cv: float = 0.3, trend: float = 0.0, seed: int = 1
) -> list[float]:
    """Weekly quantities around ``mean`` with noise and a linear trend (share per year)."""
    rng = random.Random(seed)
    out = []
    for i in range(weeks):
        level = mean * (1 + trend * i / 52)
        out.append(max(0.0, round(rng.gauss(level, level * cv))))
    return out


def daily_from_weekly(product_id: int, weekly: Sequence[float], end: date) -> list[DailyDemand]:
    """Spread each weekly total on one order day per week (the seed does the same)."""
    start = end - timedelta(days=len(weekly) * 7 - 1)
    rows = []
    for i, qty in enumerate(weekly):
        if qty > 0:
            day = start + timedelta(days=i * 7 + 2)
            rows.append(DailyDemand(product_id=product_id, day=day, ordered=qty, delivered=qty))
    return rows


class FakeDataPorts:
    """Whatever the test puts in; ``calls`` records what was asked."""

    def __init__(self) -> None:
        self.warehouse_ = WAREHOUSE
        self.products_: list[Product] = []
        self.demand: list[DailyDemand] = []
        self.stock: list[OnHand] = []
        self.incoming: list[IncomingLine] = []
        self.terms: list[SupplierTerms] = []
        self.rules: list[Orderpoint] = []
        self.calls: list[tuple[str, Any]] = []

    async def warehouse(self, code: str | None) -> Warehouse:
        self.calls.append(("warehouse", code))
        return self.warehouse_

    async def products(self, category: str | None) -> list[Product]:
        self.calls.append(("products", category))
        return list(self.products_)

    async def daily_sales(
        self, product_ids: Sequence[int], *, since: date, warehouse_id: int
    ) -> list[DailyDemand]:
        self.calls.append(("daily_sales", (list(product_ids), since)))
        return [d for d in self.demand if d.product_id in product_ids and d.day >= since]

    async def on_hand(self, product_ids: Sequence[int], *, warehouse_id: int) -> list[OnHand]:
        self.calls.append(("on_hand", list(product_ids)))
        return [s for s in self.stock if s.product_id in product_ids]

    async def open_supply(
        self, product_ids: Sequence[int], *, warehouse_id: int
    ) -> list[IncomingLine]:
        self.calls.append(("open_supply", list(product_ids)))
        return [line for line in self.incoming if line.product_id in product_ids]

    async def supplier_terms(self, product_ids: Sequence[int]) -> list[SupplierTerms]:
        self.calls.append(("supplier_terms", list(product_ids)))
        return [t for t in self.terms if t.product_id in product_ids]

    async def orderpoints(self, warehouse_id: int) -> list[Orderpoint]:
        self.calls.append(("orderpoints", warehouse_id))
        return list(self.rules)


def demo_ports(*, as_of: date, weeks: int = 104) -> FakeDataPorts:
    """A tiny Sun Hydraulics catalogue: stable, trending, intermittent and unsupplied products."""
    ports = FakeDataPorts()
    end = as_of - timedelta(days=1)
    catalogue = [
        (1, "CBEA-LHN", "Contrabalance 3:1 T-11A", 7.0, 0.35, 0.08),
        (2, "CXDA-XCN", "Check T-10A", 9.0, 0.30, 0.02),
        (3, "LODC-XDN", "Logic element T-11A", 1.0, 0.9, 0.0),
        (4, "990-011-007", "Kit de sellos T-11A", 12.0, 0.25, 0.0),
    ]
    for pid, ref, name, mean, cv, trend in catalogue:
        ports.products_.append(product(pid, ref, name))
        ports.demand += daily_from_weekly(
            pid, weekly_series(weeks=weeks, mean=mean, cv=cv, trend=trend, seed=pid), end
        )
    ports.stock = [
        OnHand(product_id=1, warehouse_id=1, quantity=20, reserved=2),
        OnHand(product_id=2, warehouse_id=1, quantity=90),
        OnHand(product_id=3, warehouse_id=1, quantity=3),
        OnHand(product_id=4, warehouse_id=1, quantity=400),
    ]
    ports.incoming = [
        IncomingLine(
            line_id=1,
            product_id=1,
            po_id=66,
            po_name="P00066",
            partner_id=PRIMARY.id,
            quantity=10,
            date_planned=as_of + timedelta(days=12),
        )
    ]
    ports.terms = [
        SupplierTerms(
            product_id=1,
            partner_id=PRIMARY.id,
            partner_name=PRIMARY.name,
            delay_days=30,
            min_qty=1,
            price=104.0,
            currency="PEN",
            sequence=1,
        ),
        SupplierTerms(
            product_id=1,
            partner_id=ALTERNATE.id,
            partner_name=ALTERNATE.name,
            delay_days=18,
            min_qty=1,
            price=114.0,
            currency="PEN",
            sequence=2,
        ),
        SupplierTerms(
            product_id=2,
            partner_id=PRIMARY.id,
            partner_name=PRIMARY.name,
            delay_days=25,
            min_qty=5,
            price=27.0,
            currency="PEN",
            sequence=1,
        ),
        SupplierTerms(
            product_id=4,
            partner_id=PRIMARY.id,
            partner_name=PRIMARY.name,
            delay_days=25,
            min_qty=5,
            price=9.0,
            currency="PEN",
            sequence=1,
        ),
    ]
    ports.rules = [
        Orderpoint(
            id=1,
            name="OP/1",
            product_id=Ref(id=1, name="CBEA-LHN"),
            warehouse_id=Ref(id=1, name="WH"),
            location_id=Ref(id=8, name="WH/Stock"),
            product_min_qty=4,
            product_max_qty=56,
        ),
        Orderpoint(
            id=2,
            name="OP/2",
            product_id=Ref(id=2, name="CXDA-XCN"),
            warehouse_id=Ref(id=1, name="WH"),
            location_id=Ref(id=8, name="WH/Stock"),
            product_min_qty=27,
            product_max_qty=72,
        ),
        Orderpoint(
            id=4,
            name="OP/4",
            product_id=Ref(id=4, name="990-011-007"),
            warehouse_id=Ref(id=1, name="WH"),
            location_id=Ref(id=8, name="WH/Stock"),
            product_min_qty=36,
            product_max_qty=360,
        ),
    ]
    return ports


class FakeWritePorts:
    """Records writes; RFQs are idempotent on the external ref like Odoo's are."""

    def __init__(self) -> None:
        self.orderpoints: list[dict[str, Any]] = []
        self.rfqs: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self._next_rule = 100
        self._next_po = 70

    async def set_orderpoint(
        self,
        *,
        product_id: int,
        warehouse_id: int,
        minimum: float,
        maximum: float,
        existing_id: int | None,
    ) -> int:
        rule_id = existing_id if existing_id is not None else self._next_rule
        if existing_id is None:
            self._next_rule += 1
        self.orderpoints.append(
            {
                "id": rule_id,
                "product_id": product_id,
                "warehouse_id": warehouse_id,
                "min": minimum,
                "max": maximum,
                "created": existing_id is None,
            }
        )
        return rule_id

    async def create_rfq(
        self,
        *,
        partner_id: int,
        lines: list[NewOrderLine],
        external_ref: str,
        origin: str | None = None,
    ) -> tuple[int, str, bool]:
        if external_ref in self.rfqs:
            po = self.rfqs[external_ref]
            return po["id"], po["name"], False
        po_id = self._next_po
        self._next_po += 1
        self.rfqs[external_ref] = {
            "id": po_id,
            "name": f"P{po_id:05d}",
            "partner_id": partner_id,
            "lines": [ln.model_dump(mode="json") for ln in lines],
            "origin": origin,
        }
        return po_id, f"P{po_id:05d}", True

    async def start_run(self, **fields: Any) -> None:
        self.runs[fields["run_id"]] = {**fields, "status": "running"}

    async def finish_run(self, run_id: str, *, status: str, summary: str) -> None:
        self.runs.setdefault(run_id, {})["status"] = status
        self.runs[run_id]["summary"] = summary


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> bool:
        self.events.append(event)
        return True
