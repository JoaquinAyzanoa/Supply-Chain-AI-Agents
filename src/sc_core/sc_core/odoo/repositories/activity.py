"""Activities (``mail.activity``): the to-do a person sees in Odoo for an approval."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sc_core.odoo.models import Activity, to_odoo_date
from sc_core.odoo.repositories.base import Repo
from sc_core.shared.errors import ExternalServiceError

TODO_XMLID = "mail.mail_activity_data_todo"
# Odoo's RPC layer does not serialise recordsets; it returns their repr,
# e.g. "mail.activity(17,)". Parse it rather than rely on it.
_RECORDSET = re.compile(r"\((\d+)")


def ids_from_rpc_result(result: Any) -> list[int]:
    """Ids from an RPC result that may be an int, a list of ints or a recordset repr."""
    if isinstance(result, bool) or result is None:
        return []
    if isinstance(result, int):
        return [result]
    if isinstance(result, list):
        return [int(x) for x in result]
    if isinstance(result, str):
        return [int(m) for m in _RECORDSET.findall(result)]
    return []


class ActivityRepo(Repo[Activity]):
    model = Activity

    async def create_approval(
        self,
        *,
        res_model: str,
        res_id: int,
        user_id: int,
        summary: str,
        note_html: str,
        deadline: date,
    ) -> Activity:
        """Schedule a To-Do on a record through the record's own ``activity_schedule``.

        Going through the record (instead of creating ``mail.activity`` rows
        directly) keeps Odoo's own validation and notifications.
        """
        result = await self._c.call(
            res_model,
            "activity_schedule",
            [res_id],
            act_type_xmlid=TODO_XMLID,
            summary=summary,
            note=note_html,
            user_id=user_id,
            date_deadline=to_odoo_date(deadline),
        )
        ids = ids_from_rpc_result(result)
        if not ids:
            raise ExternalServiceError(
                "activity_schedule returned no activity id",
                service="odoo",
                details={"result": str(result)[:200]},
                retryable=False,
            )
        return await self.get(ids[0])

    async def pending_for(self, res_model: str, res_id: int) -> list[Activity]:
        return await self.find(
            [["res_model", "=", res_model], ["res_id", "=", res_id]], order="date_deadline asc"
        )

    async def mark_done(self, activity_id: int, *, feedback: str | None = None) -> None:
        """Complete the activity (it is archived into the chatter by Odoo)."""
        await self._c.call(self._name, "action_feedback", [activity_id], feedback=feedback)
