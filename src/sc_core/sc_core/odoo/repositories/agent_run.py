"""Run log (``sc.agent.run``)."""

from __future__ import annotations

from typing import Any

from sc_core.odoo.models import AgentRun, RunStatus
from sc_core.odoo.repositories.base import Repo


class AgentRunRepo(Repo[AgentRun]):
    model = AgentRun

    async def start(
        self,
        *,
        run_id: str,
        agent: str,
        case_id: str,
        po_id: int | None = None,
        model: str | None = None,
        trace_url: str | None = None,
    ) -> AgentRun:
        """Record the start of a run. Idempotent on ``run_id``."""
        if existing := await self.get_by_run_id(run_id):
            return existing
        values: dict[str, Any] = {
            "run_id": run_id,
            "agent": agent,
            "case_id": case_id,
            "status": "running",
        }
        if po_id is not None:
            values["po_id"] = po_id
        if model:
            values["model"] = model
        if trace_url:
            values["trace_url"] = trace_url
        new_id = await self._c.create(self._name, values)
        return await self.get(new_id)

    async def finish(self, run_id: str, status: RunStatus, summary: str | None = None) -> bool:
        """Close a run through the addon's ``sc_finish``. False when the run id is unknown."""
        updated = await self._c.call_model(self._name, "sc_finish", run_id, status, summary)
        return bool(updated)

    async def get_by_run_id(self, run_id: str) -> AgentRun | None:
        return await self.find_one([["run_id", "=", run_id]])

    async def for_po(self, po_id: int) -> list[AgentRun]:
        return await self.find([["po_id", "=", po_id]], order="started_at desc")
