"""The committed dataset file is consistent and the loader rejects broken ones."""

from __future__ import annotations

import pytest
from odoo_seed_lib.dataset import DEFAULT_PATH, Dataset, load
from pydantic import ValidationError


def test_committed_dataset_loads_and_is_consistent() -> None:
    ds = load(DEFAULT_PATH)
    assert len(ds.products) == 30 and set(ds.suppliers) == {"primary", "alternate", "importer"}
    assert ds.suppliers["primary"].email == "ventas.hidraulica.sc@gmail.com"
    assert ds.suppliers["alternate"].email is None  # no mailbox on purpose
    assert ds.suppliers["importer"].email is None
    codes = {p.code for p in ds.products}
    assert {"CBEA-LHN", "CBCA-LHN", "770-212", "990-011-007", "AAA-T11A", "MH-R2-12"} <= codes
    assert len({p.category for p in ds.products}) == 5
    assert all(p.suppliers["primary"].delay >= 20 for p in ds.products)
    assert sum(1 for p in ds.products if len(p.suppliers) >= 2) >= 6  # quote rounds need choice
    assert set(ds.rule_flaws) == {"CBEA-LHN", "990-011-007", "LODC-XDN"}
    assert ds.history.months == 24 and len(ds.open_supply) == 7 and len(ds.open_rfqs) == 3
    assert ds.product("CBEA-LHN").standard_price == pytest.approx(104.16)

    # demand variety: seasonal, intermittent and launched-this-year products exist
    patterns = {p.demand.pattern for p in ds.products}
    assert patterns == {"regular", "intermittent"}
    assert any(p.demand.since_months for p in ds.products)
    assert any(p.demand.seasonality == "campaign" for p in ds.products)

    # day one: about a dozen products below their reorder point, two at zero
    low = [p for p in ds.products if p.stock.today_weeks is not None and p.stock.today_weeks <= 2]
    assert len(low) >= 10
    assert sum(1 for p in ds.products if p.stock.today_weeks == 0) == 2

    # rules for class A (top revenue share) plus the flawed ones; none for LODC-XDN
    ruled = ds.ruled_codes()
    assert {"CBEA-LHN", "990-011-007"} <= ruled and "LODC-XDN" not in ruled
    assert 8 <= len(ruled) <= 14

    # suppliers deliver differently
    assert ds.receipts_for("alternate").on_time_share > ds.receipts_for("primary").on_time_share
    assert ds.receipts_for("importer").late_days_max > ds.receipts_for("primary").late_days_max


def test_loader_rejects_inconsistencies() -> None:
    raw = load(DEFAULT_PATH).model_dump()
    broken = {**raw, "products": [{**raw["products"][0], "category": "Nope"}]}
    with pytest.raises(ValidationError, match="unknown category"):
        Dataset.model_validate(broken)
    weights = {**raw, "customers": [{**raw["customers"][0], "weight": 0.5}]}
    with pytest.raises(ValidationError, match="weights must sum"):
        Dataset.model_validate(weights)
    shares = {**raw}
    shares["history"] = {
        **raw["history"],
        "receipts": {**raw["history"]["receipts"], "late_share": 0.9},
    }
    with pytest.raises(ValidationError, match="sum to 1"):
        Dataset.model_validate(shares)
    unsold = {**raw, "open_rfqs": [{"supplier": "importer", "products": ["NFCC-LCN"]}]}
    with pytest.raises(ValidationError, match="does not sell"):
        Dataset.model_validate(unsold)
    unbilled = {
        **raw,
        "open_supply": [
            {"supplier": "primary", "days_ahead": -1, "products": ["NFCC-LCN"], "bill": "clean"}
        ],
    }
    with pytest.raises(ValidationError, match="needs a received order"):
        Dataset.model_validate(unbilled)
