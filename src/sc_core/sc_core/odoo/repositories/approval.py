"""Approval requests (``sc.approval``).

The agent creates them and reads them back; humans resolve them (buttons,
controller, Control Tower). ``resolve`` exists for the Control Tower's own
Odoo user in phase 8 and for tests.
"""

from __future__ import annotations

import json
from typing import Any

from sc_core.odoo.models import Approval, ApprovalKind, ApprovalStatus
from sc_core.odoo.repositories.base import Repo
from sc_core.shared.errors import ValidationFailed


class ApprovalRepo(Repo[Approval]):
    model = Approval

    async def create(
        self,
        *,
        kind: ApprovalKind,
        summary: str,
        payload: dict[str, Any],
        requested_by: str,
        case_id: str,
        po_id: int | None = None,
        res_model: str | None = None,
        res_id: int | None = None,
        run_id: str | None = None,
        thread_id: str | None = None,
        callback_url: str | None = None,
        callback_secret: str | None = None,
    ) -> Approval:
        if po_id is None and not (res_model and res_id):
            raise ValidationFailed("an approval needs a purchase order or a record reference")
        values: dict[str, Any] = {
            "kind": kind,
            "summary": summary,
            "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
            "requested_by": requested_by,
            "case_id": case_id,
        }
        if po_id is not None:
            values["po_id"] = po_id
        if res_model and res_id:
            values.update({"res_model": res_model, "res_id": res_id})
        for key, value in {
            "run_id": run_id,
            "thread_id": thread_id or case_id,
            "callback_url": callback_url,
            "callback_secret": callback_secret,
        }.items():
            if value:
                values[key] = value
        new_id = await self._c.create(self._name, values)
        return await self.get(new_id)

    async def pending_for_po(self, po_id: int) -> list[Approval]:
        return await self.find(
            [["po_id", "=", po_id], ["status", "=", "pending"]], order="create_date asc"
        )

    async def pending(self) -> list[Approval]:
        return await self.find([["status", "=", "pending"]], order="create_date asc")

    async def resolve(
        self, approval_id: int, status: ApprovalStatus, *, reason: str | None = None
    ) -> Approval:
        """Approve or reject as the client's own user (the bot must not do this in production)."""
        if status == "approved":
            await self._c.call(self._name, "action_approve", [approval_id])
        elif status == "rejected":
            if reason:
                await self._write([approval_id], {"reason": reason})
            await self._c.call(self._name, "action_reject", [approval_id])
        else:
            raise ValidationFailed(f"cannot resolve an approval as {status!r}")
        return await self.get(approval_id)

    @staticmethod
    def payload_of(approval: Approval) -> dict[str, Any]:
        return json.loads(approval.payload_json) if approval.payload_json else {}
