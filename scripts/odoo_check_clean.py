"""Assert the local Odoo holds only our data: no Odoo demo partners or products.

    uv run python scripts/odoo_check_clean.py

Exit code 1 when a record from Odoo's own demo data is found (the furniture
catalogue, Wood Corner, Azure Interior and friends) or when the seeded
dataset is missing. Runs as the administrator.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from odoo_seed_lib.client import SeedClient  # noqa: E402
from odoo_seed_lib.dataset import load  # noqa: E402

from sc_core.infra.settings import Settings  # noqa: E402

DEMO_PARTNERS = [
    "Wood Corner",
    "Azure Interior",
    "Ready Mat",
    "Gemini Furniture",
    "Deco Addict",
    "Lumber Inc",
    "The Jackson Group",
    "Acme Corporation",  # not in Odoo's demo data, but a common leftover from manual tests
]
DEMO_CATEGORIES = ["Office Furniture", "Furniture", "Saleable / Office Furniture"]
DEMO_PRODUCT_CODES = ["FURN_%", "E-COM%", "CONS_%", "DESK%"]


async def check(password: str) -> int:
    ds = load()
    settings = Settings()
    problems: list[str] = []
    async with SeedClient.from_settings(settings, password=password) as odoo:
        partners = await odoo.search_read(
            "res.partner", [["name", "in", DEMO_PARTNERS]], ["name"], limit=50
        )
        if partners:
            problems.append("demo partners: " + ", ".join(p["name"] for p in partners))
        categories = await odoo.search_read(
            "product.category", [["name", "in", DEMO_CATEGORIES]], ["complete_name"], limit=20
        )
        if categories:
            problems.append("demo categories: " + ", ".join(c["complete_name"] for c in categories))
        for pattern in DEMO_PRODUCT_CODES:
            found = await odoo.search_count(
                "product.template", [["default_code", "=like", pattern]]
            )
            if found:
                problems.append(f"{found} demo product(s) with code {pattern}")
        own = await odoo.search_count(
            "product.template", [["default_code", "in", [p.code for p in ds.products]]]
        )
        if own != len(ds.products):
            problems.append(f"seeded products: {own} of {len(ds.products)} present")
        for key, supplier in ds.suppliers.items():
            domain = (
                [["email", "=", supplier.email]]
                if supplier.email
                else [["name", "=", supplier.name]]
            )
            if not await odoo.search_count("res.partner", domain):
                problems.append(f"supplier {key} ({supplier.name}) missing")
        products = await odoo.search_count("product.template", [["purchase_ok", "=", True]])
        suppliers = await odoo.search_count("res.partner", [["supplier_rank", ">", 0]])
        orders = await odoo.search_count("purchase.order", [])
        print(
            f"odoo: {products} purchasable products, {suppliers} suppliers, "
            f"{orders} purchase orders"
        )
    if problems:
        print("NOT CLEAN:")
        for line in problems:
            print(f"  - {line}")
        return 1
    print("clean: only the Sun Hydraulics dataset is present")
    return 0


def main() -> int:
    password = sys.argv[1] if len(sys.argv) > 1 else "admin"
    return asyncio.run(check(password))


if __name__ == "__main__":
    raise SystemExit(main())
