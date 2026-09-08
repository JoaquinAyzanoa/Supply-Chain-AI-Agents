"""Client smoke tests against the real Odoo container."""

import pytest

from sc_core.infra.health import HealthRegistry, OverallStatus, checks
from sc_core.odoo.client import OdooClient
from sc_core.shared.errors import Forbidden, NotFound

pytestmark = [pytest.mark.integration, pytest.mark.odoo]


async def test_version_and_login(odoo_client: OdooClient) -> None:
    version = await odoo_client.version()
    assert version["server_version"].startswith("18.")
    assert await odoo_client.uid() > 0


async def test_demo_data_is_visible(odoo_client: OdooClient) -> None:
    assert await odoo_client.search_count("purchase.order", []) > 0
    rows = await odoo_client.search_read(
        "purchase.order", [["state", "=", "purchase"]], ["name", "partner_id"], limit=1
    )
    assert rows and rows[0]["name"].startswith("P")
    assert isinstance(rows[0]["partner_id"], list)  # [id, display_name]


async def test_bot_privileges_are_limited(odoo_client: OdooClient) -> None:
    with pytest.raises(Forbidden):
        await odoo_client.search_read("ir.config_parameter", [], ["key"], limit=1)
    # Odoo 18: read() silently drops missing ids, but writes and record methods raise.
    assert await odoo_client.read("purchase.order", [999_999_999], ["name"]) == []
    with pytest.raises(NotFound):
        await odoo_client.write("purchase.order", [999_999_999], {"sc_needs_human": True})


async def test_pagination_matches_count(odoo_client: OdooClient) -> None:
    total = await odoo_client.search_count("product.product", [])
    rows = [
        r async for r in odoo_client.iter_search_read("product.product", [], ["id"], batch_size=7)
    ]
    assert len(rows) == total
    assert len({r["id"] for r in rows}) == total


async def test_health_check(odoo_client: OdooClient) -> None:
    reg = HealthRegistry()
    reg.register("odoo", checks.odoo(odoo_client))
    report = await reg.run(service="t", version="1")
    assert report.status is OverallStatus.OK, report.model_dump()
