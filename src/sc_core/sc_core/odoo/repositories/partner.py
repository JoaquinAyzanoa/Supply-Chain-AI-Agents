"""Suppliers and their contacts (``res.partner``)."""

from __future__ import annotations

from typing import Any

from sc_core.odoo.models import Partner
from sc_core.odoo.repositories.base import Repo


class PartnerRepo(Repo[Partner]):
    model = Partner

    async def suppliers(self) -> list[Partner]:
        """Companies that have been used as vendors at least once."""
        return await self.find(
            [["supplier_rank", ">", 0], ["is_company", "=", True]], order="name asc"
        )

    async def find_by_email(self, email: str) -> Partner | None:
        """Exact match on the normalised address (any contact, company or person)."""
        return await self.find_one([["email_normalized", "=", email.strip().lower()]])

    async def find_by_email_domain(self, domain: str) -> list[Partner]:
        """Every partner whose address ends with ``@domain``; companies first."""
        found = await self.find(
            [["email_normalized", "=ilike", f"%@{domain.strip().lower()}"]],
            order="is_company desc, name asc",
        )
        return found

    async def commercial_partner(self, partner: Partner) -> Partner:
        """The company a contact belongs to (itself when already a company)."""
        if partner.commercial_partner_id is None or partner.commercial_partner_id.id == partner.id:
            return partner
        return await self.get(partner.commercial_partner_id.id)

    async def contacts_of(self, company_id: int) -> list[Partner]:
        return await self.find([["parent_id", "=", company_id]], order="name asc")

    async def create_supplier(
        self, *, name: str, email: str | None, lang: str | None = None, comment: str | None = None
    ) -> Partner:
        """A new vendor company (``supplier_rank`` 1). Idempotent on the email address."""
        if email and (existing := await self.find_by_email(email)):
            return await self.commercial_partner(existing)
        values: dict[str, Any] = {
            "name": name.strip(),
            "is_company": True,
            "supplier_rank": 1,
        }
        if email:
            values["email"] = email.strip().lower()
        if lang:
            values["lang"] = lang
        if comment:
            values["comment"] = comment
        partner_id = await self._c.create(self._name, values)
        return await self.get(int(partner_id))

    async def emails_of(self, company_id: int) -> list[str]:
        """Normalised addresses of a company and its contacts, de-duplicated."""
        company = await self.get(company_id)
        people = await self.contacts_of(company_id)
        seen: dict[str, None] = {}
        for p in [company, *people]:
            if p.email_normalized:
                seen.setdefault(p.email_normalized, None)
        return list(seen)
