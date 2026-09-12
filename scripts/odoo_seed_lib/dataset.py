"""The dataset file as typed models, validated on load."""

from __future__ import annotations

from pathlib import Path

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


class Supplier(Strict):
    name: str
    email: str | None = None
    country: str = "PE"
    city: str | None = None
    lang: str | None = None


class Customer(Strict):
    name: str
    city: str | None = None
    weight: float = Field(gt=0)


class Demand(Strict):
    mean_weekly: float = Field(gt=0)
    cv: float = Field(ge=0, le=2)
    trend_per_year: float = Field(ge=-0.9, le=2)
    seasonality: str


class StockTargets(Strict):
    weeks_on_hand: float = Field(ge=0)
    min_weeks: float = Field(ge=0)
    max_weeks: float = Field(gt=0)


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


class RuleFlaw(Strict):
    min_weeks: float | None = None
    max_weeks: float | None = None
    no_rule: bool = False


class Receipts(Strict):
    on_time_share: float = Field(ge=0, le=1)
    late_share: float = Field(ge=0, le=1)
    partial_share: float = Field(ge=0, le=1)
    late_days_max: int = Field(ge=1)

    @model_validator(mode="after")
    def _sums_to_one(self) -> Receipts:
        if abs(self.on_time_share + self.late_share + self.partial_share - 1) > 1e-6:
            raise ValueError("receipt shares must sum to 1")
        return self


class History(Strict):
    months: int = Field(ge=1, le=60)
    backorder_share: float = Field(ge=0, le=1)
    receipts: Receipts


class OpenSupply(Strict):
    supplier: str
    days_ahead: int
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
    products: list[Product]
    rule_flaws: dict[str, RuleFlaw] = {}
    history: History
    open_supply: list[OpenSupply] = []

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
        for code in self.rule_flaws:
            if code not in codes:
                raise ValueError(f"rule flaw for unknown product {code!r}")
        for entry in self.open_supply:
            if entry.supplier not in self.suppliers:
                raise ValueError(f"open supply: unknown supplier {entry.supplier!r}")
            for code in entry.products:
                if code not in codes:
                    raise ValueError(f"open supply: unknown product {code!r}")
        if abs(sum(c.weight for c in self.customers) - 1) > 1e-6:
            raise ValueError("customer weights must sum to 1")
        return self

    def product(self, code: str) -> Product:
        for p in self.products:
            if p.code == code:
                return p
        raise KeyError(code)


def load(path: Path | None = None) -> Dataset:
    raw = yaml.safe_load((path or DEFAULT_PATH).read_text(encoding="utf-8"))
    return Dataset.model_validate(raw)
