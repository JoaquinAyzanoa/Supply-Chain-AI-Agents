"""Create the demo supplier and an open RFQ for it in the local Odoo (idempotent).

    python scripts/odoo_demo_supplier.py [--email ventas.hidraulica.sc@gmail.com]
        [--name "Proveedor Hidraulica"]

Prints the partner id and the RFQ name the phase 5 end-to-end test uses.
The partner is created as the administrator (the bot deliberately cannot
create partners); the RFQ is created as the bot, with the agents' rights.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "sc_core"))

from sc_core.infra.settings import Settings  # noqa: E402
from sc_core.odoo.client import OdooClient  # noqa: E402
from sc_core.odoo.models import NewOrderLine  # noqa: E402
from sc_core.odoo.repositories import PartnerRepo, PurchaseOrderRepo  # noqa: E402

DEFAULT_EMAIL = "ventas.hidraulica.sc@gmail.com"
DEFAULT_NAME = "Proveedor Hidraulica"


async def ensure(email: str, name: str) -> int:
    settings = Settings()
    if not settings.odoo.configured:
        print("SC__ODOO__API_KEY not set; run `just odoo-apikey`", file=sys.stderr)
        return 2
    async with OdooClient(settings.odoo) as odoo:
        partners, orders = PartnerRepo(odoo), PurchaseOrderRepo(odoo)
        partner = await partners.find_by_email(email)
        if partner is None:
            values = {"name": name, "email": email, "is_company": True, "supplier_rank": 1}
            if await _lang_installed(odoo, "es_PE"):
                values["lang"] = "es_PE"
            admin_cfg = settings.odoo.model_copy(
                update={"login": "admin", "api_key": SecretStr("admin")}
            )
            async with OdooClient(admin_cfg) as admin:
                partner_id = await admin.create("res.partner", values)
            print(f"created partner {partner_id} ({name} <{email}>)")
        else:
            partner_id = (await partners.commercial_partner(partner)).id
            print(f"partner exists: {partner_id} ({partner.name})")
        open_orders = await orders.open_for_partner(partner_id)
        if open_orders:
            print(f"open order for the supplier: {open_orders[0].name}")
            return 0
        products = await odoo.search_read(
            "product.product",
            [["purchase_ok", "=", True], ["type", "in", ["consu", "product", "goods"]]],
            ["id", "display_name"],
            limit=2,
            order="id asc",
        )
        if not products:
            print("no purchasable product found; install the demo data", file=sys.stderr)
            return 1
        rfq = await orders.create_rfq(
            partner_id=partner_id,
            lines=[
                NewOrderLine(product_id=int(p["id"]), product_qty=float(2 + i))
                for i, p in enumerate(products)
            ],
            external_ref=f"demo-supplier-{partner_id}",
        )
        print(f"created RFQ {rfq.name} with {len(products)} line(s)")
        return 0


async def _lang_installed(odoo: OdooClient, code: str) -> bool:
    rows = await odoo.search_read("res.lang", [["code", "=", code], ["active", "=", True]], ["id"])
    return bool(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()
    return asyncio.run(ensure(args.email, args.name))


if __name__ == "__main__":
    raise SystemExit(main())
