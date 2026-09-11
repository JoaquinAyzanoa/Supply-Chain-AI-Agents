"""Shared plumbing for repositories."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sc_core.odoo.client import Domain, OdooClient
from sc_core.odoo.models import OdooModel
from sc_core.shared.errors import NotFound


class Repo[M: OdooModel]:
    """Read helpers bound to one model class. Subclasses add the domain-specific API."""

    model: type[M]

    def __init__(self, client: OdooClient) -> None:
        self._c = client

    @property
    def _name(self) -> str:
        return self.model.ODOO_MODEL

    async def get(self, record_id: int) -> M:
        rows = await self._c.read(self._name, [record_id], self.model.odoo_fields())
        if not rows:  # Odoo 18 read() drops missing ids instead of raising
            raise NotFound(f"{self._name} {record_id} not found", details={"model": self._name})
        return self.model.from_odoo(rows[0])

    async def get_many(self, ids: Sequence[int]) -> list[M]:
        if not ids:
            return []
        rows = await self._c.read(self._name, list(ids), self.model.odoo_fields())
        return [self.model.from_odoo(r) for r in rows]

    async def find(
        self, domain: Domain, *, limit: int | None = None, order: str | None = None
    ) -> list[M]:
        rows = await self._c.search_read(
            self._name, domain, self.model.odoo_fields(), limit=limit, order=order
        )
        return [self.model.from_odoo(r) for r in rows]

    async def find_one(self, domain: Domain, *, order: str | None = None) -> M | None:
        found = await self.find(domain, limit=1, order=order)
        return found[0] if found else None

    async def count(self, domain: Domain) -> int:
        return await self._c.search_count(self._name, domain)

    async def _write(self, ids: Sequence[int], values: dict[str, Any]) -> None:
        await self._c.write(self._name, ids, values)
