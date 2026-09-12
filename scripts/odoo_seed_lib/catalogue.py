"""Company, language, categories and products."""

from __future__ import annotations

from dataclasses import dataclass

from odoo_seed_lib.client import SeedClient
from odoo_seed_lib.dataset import Dataset


@dataclass
class ProductIds:
    code: str
    product_id: int  # product.product
    template_id: int  # product.template


async def ensure_language(client: SeedClient, code: str) -> None:
    """Activate ``code`` (installs translations) when it is not active yet."""
    odoo = client.odoo
    active = await odoo.search(
        "res.lang", [["code", "=", code], ["active", "=", True]], limit=1
    )
    if active:
        return
    lang_ids = await odoo.execute(
        "res.lang", "search", [["code", "=", code]], limit=1, context={"active_test": False}
    )
    if not lang_ids:
        raise RuntimeError(f"language {code!r} does not exist in this Odoo")
    wizard = await odoo.create("base.language.install", {"lang_ids": [(6, 0, lang_ids)]})
    await odoo.call("base.language.install", "lang_install", [wizard])
    client.created["res.lang"] = client.created.get("res.lang", 0) + 1


async def ensure_company(client: SeedClient, ds: Dataset) -> int:
    odoo = client.odoo
    company_ids = await odoo.search("res.company", [], limit=1, order="id asc")
    company_id = int(company_ids[0])
    country_id = await client.find_id("res.country", [["code", "=", ds.company.country]])
    values = {
        "name": ds.company.name,
        "street": ds.company.street,
        "city": ds.company.city,
        "country_id": country_id,
        "phone": ds.company.phone,
        "email": ds.company.email,
        "vat": ds.company.vat,
    }
    await odoo.write("res.company", [company_id], {k: v for k, v in values.items() if v})
    partner = await odoo.read("res.company", [company_id], ["partner_id"])
    partner_id = int(partner[0]["partner_id"][0])
    await odoo.write("res.partner", [partner_id], {"lang": ds.language})
    client.found["res.company"] = 1
    return company_id


async def ensure_categories(client: SeedClient, ds: Dataset) -> dict[str, int]:
    """Categories named like ``Hidráulica / Cartuchos``; parents first."""
    ids: dict[str, int] = {}
    for full in ds.categories:
        parts = [p.strip() for p in full.split("/")]
        parent_id = ids.get(" / ".join(parts[:-1])) if len(parts) > 1 else None
        domain = [["name", "=", parts[-1]], ["parent_id", "=", parent_id or False]]
        values = {"name": parts[-1], "parent_id": parent_id}
        ids[full] = await client.find_or_create("product.category", domain, values)
    return ids


async def ensure_products(
    client: SeedClient, ds: Dataset, categories: dict[str, int]
) -> dict[str, ProductIds]:
    odoo = client.odoo
    uom_id = await client.find_id("uom.uom", [["name", "in", ["Units", "Unidades"]]])
    out: dict[str, ProductIds] = {}
    for p in ds.products:
        values = {
            "name": p.name,
            "default_code": p.code,
            "categ_id": categories[p.category],
            "type": "consu",
            "is_storable": True,
            "tracking": "none",
            "list_price": p.list_price,
            "standard_price": p.standard_price,
            "weight": p.weight_kg,
            "purchase_ok": True,
            "sale_ok": True,
            "uom_id": uom_id,
            "uom_po_id": uom_id,
            "description_purchase": "Marca Sun Hydraulics. Indicar número de parte en la cotización.",
        }
        template_id = await client.find_or_create(
            "product.template", [["default_code", "=", p.code]], values, update=True
        )
        variants = await odoo.search(
            "product.product", [["product_tmpl_id", "=", template_id]], limit=1
        )
        out[p.code] = ProductIds(p.code, int(variants[0]), template_id)
    return out
