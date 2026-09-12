"""The committed dataset file is consistent and the loader rejects broken ones."""

from __future__ import annotations

import pytest
from odoo_seed_lib.dataset import DEFAULT_PATH, Dataset, load
from pydantic import ValidationError


def test_committed_dataset_loads_and_is_consistent() -> None:
    ds = load(DEFAULT_PATH)
    assert len(ds.products) == 14 and {"primary", "alternate"} <= set(ds.suppliers)
    assert ds.suppliers["alternate"].email is None  # no mailbox on purpose
    codes = {p.code for p in ds.products}
    assert {"CBEA-LHN", "CBCA-LHN", "770-212", "990-011-007", "AAA-T11A"} <= codes
    assert all(p.suppliers["primary"].delay >= 20 for p in ds.products)
    assert set(ds.rule_flaws) == {"CBEA-LHN", "990-011-007", "LODC-XDN"}
    assert ds.history.months == 24 and len(ds.open_supply) == 3
    assert ds.product("CBEA-LHN").standard_price == pytest.approx(104.16)


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
