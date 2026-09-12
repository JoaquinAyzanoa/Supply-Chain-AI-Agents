"""Run log (``sc.agent.run``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
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

    async def finish(
        self,
        run_id: str,
        status: RunStatus,
        summary: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> bool:
        """Close a run through the addon's ``sc_finish``. False when the run id is unknown.

        ``usage`` (calls, input_tokens, output_tokens, usd) is added to the
        row's totals, so a run paused on an approval and resumed later sums
        both halves.
        """
        args: list[Any] = [run_id, status, summary]
        if usage:
            args.append(usage)
        updated = await self._c.call_model(self._name, "sc_finish", *args)
        return bool(updated)

    async def get_by_run_id(self, run_id: str) -> AgentRun | None:
        return await self.find_one([["run_id", "=", run_id]])

    async def for_po(self, po_id: int) -> list[AgentRun]:
        return await self.find([["po_id", "=", po_id]], order="started_at desc")

    async def for_cases(self, case_ids: Sequence[str]) -> list[AgentRun]:
        if not case_ids:
            return []
        return await self.find([["case_id", "in", list(case_ids)]], order="started_at desc")

    async def recent(
        self,
        *,
        agent: str | None = None,
        model: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        domain: list[Any] = []
        if agent:
            domain.append(["agent", "=", agent])
        if model:
            domain.append(["model", "=", model])
        if status:
            domain.append(["status", "=", status])
        if since is not None:
            domain.append(["started_at", ">=", since.strftime("%Y-%m-%d %H:%M:%S")])
        return await self.find(domain, order="started_at desc, id desc", limit=limit)
