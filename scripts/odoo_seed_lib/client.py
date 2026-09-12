"""Administrator session on the local Odoo with find-or-create helpers.

The seed runs as the administrator on purpose: creating partners, products
and history is set-up work, not something the agents' bot user may do.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from sc_core.infra.settings import Settings
from sc_core.odoo.client import Domain, OdooClient


class SeedClient:
    def __init__(self, odoo: OdooClient) -> None:
        self.odoo = odoo
        self.created: dict[str, int] = {}
        self.found: dict[str, int] = {}

    @classmethod
    def from_settings(cls, settings: Settings, *, password: str = "admin") -> OdooClient:
        cfg = settings.odoo.model_copy(update={"login": "admin", "api_key": SecretStr(password)})
        return OdooClient(cfg)

    async def find_id(self, model: str, domain: Domain, **kwargs: Any) -> int | None:
        ids = await self.odoo.search(model, domain, limit=1, **kwargs)
        return int(ids[0]) if ids else None

    async def find_or_create(
        self, model: str, domain: Domain, values: dict[str, Any], *, update: bool = False
    ) -> int:
        """Return the id matching ``domain``; create it with ``values`` otherwise."""
        existing = await self.find_id(model, domain)
        if existing is not None:
            if update:
                await self.odoo.write(model, [existing], values)
            self.found[model] = self.found.get(model, 0) + 1
            return existing
        new_id = await self.odoo.create(model, values)
        self.created[model] = self.created.get(model, 0) + 1
        return new_id

    def summary(self) -> str:
        models = sorted(set(self.created) | set(self.found))
        rows = [
            f"  {m}: created {self.created.get(m, 0)}, existing {self.found.get(m, 0)}"
            for m in models
        ]
        return "\n".join(rows) if rows else "  nothing"
