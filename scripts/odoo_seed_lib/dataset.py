"""The dataset file as typed models, validated on load."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "odoo" / "demo" / "sun_hydraulics.yaml"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Company(Strict):
    name: str
    street: str | None = None
    city: str | None = None
    country: str = "PE"
    phone: str | None = None
    email: str | None = None
    vat: str | None = None


class Receipts(Strict):
    """How a supplier's receipts go: shares of on time, late and partial deliveries."""

    on_time_share: float = Field(ge=0, le=1)
    late_share: float = Field(ge=0, le=1)
    partial_share: float = Field(ge=0, le=1)
    late_days_max: int = Field(ge=1)

    @model_validator(mode="after")
    def _sums_to_one(self) -> Receipts:
        if abs(self.on_time_share + self.late_share + self.partial_share - 1) > 1e-6:
            raise ValueError("receipt shares must sum to 1")
        return self


class Supplier(Strict):
    name: str
    email: str | None = None
    country: str = "PE"
    city: str | None = None
    lang: str | None = None
    receipts: Receipts | None = None  # None: the history's default


class Customer(Strict):
    name: str
    city: str | None = None
    weight: float = Field(gt=0)


class Demand(Strict):
    mean_weekly: float = Field(gt=0)
    cv: float = Field(ge=0, le=2)
    trend_per_year: float = Field(ge=-0.9, le=2)
    seasonality: str
    pattern: Literal["regular", "intermittent"] = "regular"
    hit_rate: float = Field(default=1.0, gt=0, le=1)  # intermittent: share of weeks with a sale
    since_months: int | None = Field(default=None, ge=1)  # a product launched recently


class StockTargets(Strict):
    weeks_on_hand: float = Field(ge=0)  # opening stock, two years ago
    min_weeks: float = Field(ge=0)
    max_weeks: float = Field(gt=0)
    today_weeks: float | None = Field(
        default=None, ge=0
    )  # on hand on day one; None: whatever history left


class SupplierTerms(Strict):
    price_ratio: float = Field(gt=0, lt=1.5)
    delay: int = Field(ge=1)
    min_qty: float = Field(ge=0)


class Product(Strict):
    code: str
    name: str
    category: str
    list_price: float = Field(gt=0)
    cost_ratio: float = Field(gt=0, lt=1)
    weight_kg: float = Field(ge=0)
    demand: Demand
    stock: StockTargets
    suppliers: dict[str, SupplierTerms]

    @property
    def standard_price(self) -> float:
        return round(self.list_price * self.cost_ratio, 2)

    @property
    def yearly_revenue(self) -> float:
        return self.list_price * self.demand.mean_weekly * 52


class RuleFlaw(Strict):
    min_weeks: float | None = None
    max_weeks: float | None = None
    no_rule: bool = False


class Rules(Strict):
    top_revenue_share: float = Field(default=1.0, gt=0, le=1)  # products that get a reorder rule


class History(Strict):
    months: int = Field(ge=1, le=60)
    backorder_share: float = Field(ge=0, le=1)
    receipts: Receipts


class OpenSupply(Strict):
    supplier: str
    days_ahead: int
    products: list[str]
    received: Literal["full"] | None = None
    received_days_ago: int = Field(default=0, ge=0)
    bill: Literal["clean", "variance"] | None = None
    bill_ref: str | None = None

    @model_validator(mode="after")
    def _bill_needs_receipt(self) -> OpenSupply:
        if self.bill and not self.received:
            raise ValueError("a bill needs a received order")
        if self.bill and not self.bill_ref:
            raise ValueError("a bill needs a bill_ref (the supplier's invoice number)")
        return self


class OpenRfq(Strict):
    supplier: str
    state: Literal["draft", "sent"] = "draft"
    days_ago: int = Field(default=0, ge=0)
    products: list[str]


class Dataset(Strict):
    seed: int
    language: str
    company: Company
    warehouse: str
    categories: list[str]
    seasonality_profiles: dict[str, list[float]]
    suppliers: dict[str, Supplier]
    customers: list[Customer]
    rules: Rules = Rules()
    products: list[Product]
    rule_flaws: dict[str, RuleFlaw] = {}
    history: History
    open_supply: list[OpenSupply] = []
    open_rfqs: list[OpenRfq] = []

    @model_validator(mode="after")
    def _consistent(self) -> Dataset:
        codes = [p.code for p in self.products]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate product codes")
        for name, factors in self.seasonality_profiles.items():
            if len(factors) != 12:
                raise ValueError(f"seasonality profile {name!r} needs 12 factors")
        for p in self.products:
            if p.category not in self.categories:
                raise ValueError(f"{p.code}: unknown category {p.category!r}")
            if p.demand.seasonality not in self.seasonality_profiles:
                raise ValueError(f"{p.code}: unknown seasonality {p.demand.seasonality!r}")
            for key in p.suppliers:
                if key not in self.suppliers:
                    raise ValueError(f"{p.code}: unknown supplier {key!r}")
            if "primary" not in p.suppliers:
                raise ValueError(f"{p.code}: needs primary supplier terms")
            if p.demand.since_months is not None and p.demand.since_months > self.history.months:
                raise ValueError(f"{p.code}: since_months exceeds the history")
        for code in self.rule_flaws:
            if code not in codes:
                raise ValueError(f"rule flaw for unknown product {code!r}")
        for entry in [*self.open_supply, *self.open_rfqs]:
            if entry.supplier not in self.suppliers:
                raise ValueError(f"open order: unknown supplier {entry.supplier!r}")
            for code in entry.products:
                if code not in codes:
                    raise ValueError(f"open order: unknown product {code!r}")
                if entry.supplier not in self.product(code).suppliers:
                    raise ValueError(f"open order: {entry.supplier!r} does not sell {code}")
        refs = [e.bill_ref for e in self.open_supply if e.bill_ref]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate bill_ref")
        if abs(sum(c.weight for c in self.customers) - 1) > 1e-6:
            raise ValueError("customer weights must sum to 1")
        return self

    def product(self, code: str) -> Product:
        for p in self.products:
            if p.code == code:
                return p
        raise KeyError(code)

    def receipts_for(self, supplier: str) -> Receipts:
        return self.suppliers[supplier].receipts or self.history.receipts

    def ruled_codes(self) -> set[str]:
        """Products with a reorder rule: the top revenue share (class A) and the flawed ones."""
        ranked = sorted(self.products, key=lambda p: (-p.yearly_revenue, p.code))
        total = sum(p.yearly_revenue for p in ranked) or 1.0
        out: set[str] = set()
        cumulative = 0.0
        for p in ranked:
            if cumulative >= self.rules.top_revenue_share * total:
                break
            out.add(p.code)
            cumulative += p.yearly_revenue
        for code, flaw in self.rule_flaws.items():
            if flaw.no_rule:
                out.discard(code)
            else:
                out.add(code)
        return out


def load(path: Path | None = None) -> Dataset:
    raw = yaml.safe_load((path or DEFAULT_PATH).read_text(encoding="utf-8"))
    return Dataset.model_validate(raw)
