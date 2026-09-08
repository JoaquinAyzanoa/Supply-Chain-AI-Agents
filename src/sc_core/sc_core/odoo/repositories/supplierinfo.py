"""Supplier price lists (``product.supplierinfo``)."""

from __future__ import annotations

from typing import Any

from sc_core.odoo.models import Product, SupplierInfo
from sc_core.odoo.repositories.base import Repo


class SupplierInfoRepo(Repo[SupplierInfo]):
    model = SupplierInfo

    async def for_product(self, product_id: int) -> list[SupplierInfo]:
        """Every supplier entry that applies to a product variant.

        Entries are attached to the template, optionally narrowed to one
        variant, so both must be searched.
        """
        rows = await self._c.read(Product.ODOO_MODEL, [product_id], ["product_tmpl_id"])
        if not rows:
            return []
        product = Product.model_validate(
            {**rows[0], "name": "", "product_tmpl_id": rows[0]["product_tmpl_id"]}
        )
        return await self.find(
            [
                ["product_tmpl_id", "=", product.product_tmpl_id.id],
                "|",
                ["product_id", "=", False],
                ["product_id", "=", product_id],
            ],
            order="sequence asc, min_qty asc",
        )

    async def for_partner(self, partner_id: int) -> list[SupplierInfo]:
        return await self.find([["partner_id", "=", partner_id]], order="product_tmpl_id asc")

    async def upsert_price(
        self,
        *,
        partner_id: int,
        product_tmpl_id: int,
        price: float,
        currency_id: int,
        min_qty: float = 0.0,
        delay: int | None = None,
        product_id: int | None = None,
    ) -> SupplierInfo:
        """Update the entry for (partner, template, variant, min_qty) or create it."""
        domain: list[Any] = [
            ["partner_id", "=", partner_id],
            ["product_tmpl_id", "=", product_tmpl_id],
            ["product_id", "=", product_id if product_id is not None else False],
            ["min_qty", "=", min_qty],
        ]
        values: dict[str, Any] = {"price": price, "currency_id": currency_id}
        if delay is not None:
            values["delay"] = delay
        existing = await self.find_one(domain)
        if existing:
            await self._write([existing.id], values)
            return await self.get(existing.id)
        values.update(
            {
                "partner_id": partner_id,
                "product_tmpl_id": product_tmpl_id,
                "product_id": product_id,
                "min_qty": min_qty,
            }
        )
        if delay is None:
            values["delay"] = 1
        new_id = await self._c.create(self._name, values)
        return await self.get(new_id)
