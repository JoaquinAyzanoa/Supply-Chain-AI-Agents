"""Suppliers with their terms, and customers."""

from __future__ import annotations

from odoo_seed_lib.catalogue import ProductIds
from odoo_seed_lib.client import SeedClient
from odoo_seed_lib.dataset import Dataset


async def ensure_suppliers(client: SeedClient, ds: Dataset) -> dict[str, int]:
    ids: dict[str, int] = {}
    for key, s in ds.suppliers.items():
        country_id = await client.find_id("res.country", [["code", "=", s.country]])
        domain = [["email", "=", s.email]] if s.email else [["name", "=", s.name]]
        values = {
            "name": s.name,
            "email": s.email,
            "is_company": True,
            "supplier_rank": 1,
            "country_id": country_id,
            "city": s.city,
            "lang": s.lang or ds.language,
        }
        ids[key] = await client.find_or_create("res.partner", domain, values, update=True)
    return ids


async def ensure_supplierinfo(
    client: SeedClient,
    ds: Dataset,
    suppliers: dict[str, int],
    products: dict[str, ProductIds],
) -> int:
    """One ``product.supplierinfo`` per (supplier, product) with price, MOQ and lead days."""
    currency_id = await client.find_id("res.currency", [["name", "=", "USD"]])
    rows = 0
    for p in ds.products:
        for key, terms in p.suppliers.items():
            domain = [
                ["partner_id", "=", suppliers[key]],
                ["product_tmpl_id", "=", products[p.code].template_id],
                ["min_qty", "=", terms.min_qty],
            ]
            values = {
                "partner_id": suppliers[key],
                "product_tmpl_id": products[p.code].template_id,
                "product_code": p.code,
                "product_name": p.name,
                "price": round(p.list_price * terms.price_ratio, 2),
                "currency_id": currency_id,
                "min_qty": terms.min_qty,
                "delay": terms.delay,
            }
            await client.find_or_create("product.supplierinfo", domain, values, update=True)
            rows += 1
    return rows


async def ensure_customers(client: SeedClient, ds: Dataset) -> dict[str, int]:
    ids: dict[str, int] = {}
    for c in ds.customers:
        country_id = await client.find_id("res.country", [["code", "=", "PE"]])
        values = {
            "name": c.name,
            "is_company": True,
            "customer_rank": 1,
            "country_id": country_id,
            "city": c.city,
            "lang": ds.language,
        }
        ids[c.name] = await client.find_or_create(
            "res.partner", [["name", "=", c.name], ["is_company", "=", True]], values
        )
    return ids
