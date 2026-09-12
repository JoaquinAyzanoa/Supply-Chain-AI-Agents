"""planning_params against a real Postgres (migration 005)."""

from __future__ import annotations

import pytest

from inventory_planning.policy import PostgresParamsStore, ProductParams
from sc_core.infra.db import Database

pytestmark = pytest.mark.integration


async def test_params_upsert_and_read(db: Database) -> None:
    store = PostgresParamsStore(db)
    assert await store.for_products([1, 2]) == {}
    first = ProductParams.default_for(1, "A")
    await store.save(first)
    await store.save(first.model_copy(update={"service_level": 0.99, "source": "planner"}))
    await store.save(
        ProductParams.default_for(2, "C").model_copy(
            update={"lead_time_mean_days": 33.5, "lead_time_sigma_days": 4.25, "source": "measured"}
        )
    )
    rows = await store.for_products([1, 2, 3])
    assert rows[1].service_level == 0.99 and rows[1].source == "planner"
    assert rows[2].lead_time_mean_days == 33.5 and rows[2].lead_time_sigma_days == 4.25
    assert 3 not in rows
