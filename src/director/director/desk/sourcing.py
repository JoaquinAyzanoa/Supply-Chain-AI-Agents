"""The director's side of sourcing: read the rounds, dispatch tasks to the agent.

``SourcingDispatcher`` runs one sourcing task on its own case the way the
jobs do: a ``task_sent`` event, the A2A call, the outcome consolidated on
the case (approval, escalation, done). The API, the hourly job and the
playbooks all go through it.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
from loguru import logger

from director.orchestration.agents import Agents
from director.orchestration.escalation import Escalator
from director.orchestration.store import CaseStore
from director.orchestration.workflow import (
    ConversationLookup,
    NoConversations,
    consolidate_outcome,
    outcome_from_reply,
)
from sc_core.schema.a2a import SourcingTask
from sc_core.shared.errors import ScError

AGENT = "sourcing"


@runtime_checkable
class SourcingSource(Protocol):
    async def rounds(
        self, *, status: str | None = None, partner_id: int | None = None
    ) -> list[dict[str, Any]]: ...

    async def round(self, round_id: int) -> dict[str, Any] | None: ...

    async def due_rounds(self) -> list[dict[str, Any]]: ...

    async def negotiations(self, po_name: str) -> list[dict[str, Any]]: ...


class HttpSourcingSource:
    """The sourcing agent's ``GET /sourcing/*`` behind its bearer token."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 30.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_seconds,
        )

    async def rounds(
        self, *, status: str | None = None, partner_id: int | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        if partner_id is not None:
            params["partner_id"] = str(partner_id)
        data: list[dict[str, Any]] = await self._get("/sourcing/rounds", params=params)
        return data

    async def round(self, round_id: int) -> dict[str, Any] | None:
        try:
            data: dict[str, Any] = await self._get(f"/sourcing/rounds/{round_id}")
        except ScError as exc:
            if (exc.details or {}).get("status") == 404:
                return None
            raise
        return data

    async def due_rounds(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = await self._get("/sourcing/rounds/due")
        return data

    async def negotiations(self, po_name: str) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = await self._get(
            "/sourcing/negotiations", params={"po_name": po_name}
        )
        return data

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        try:
            response = await self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ScError("the sourcing agent did not answer", details={"error": str(exc)}) from exc
        if response.status_code != 200:
            raise ScError(
                f"the sourcing agent answered {response.status_code}",
                details={"status": response.status_code, "path": path},
            )
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()


class SourcingDispatcher:
    """One sourcing task, on a case of its own, consolidated like any other agent run."""

    def __init__(
        self,
        *,
        cases: CaseStore,
        agents: Agents,
        escalator: Escalator,
        conversations: ConversationLookup | None = None,
    ) -> None:
        self._cases = cases
        self._agents = agents
        self._escalator = escalator
        self._conversations = conversations or NoConversations()

    async def run(
        self,
        task: SourcingTask,
        *,
        partner_id: int | None = None,
        requested_by: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        case, _ = await self._cases.attach_or_create(
            kind="sourcing", po_name=task.po_name, partner_id=partner_id, agent=AGENT
        )
        await self._cases.add_event(
            case.case_id,
            "task_sent",
            {
                "agent": AGENT,
                "task": task.kind,
                "thread_id": task.case_id,
                "po_name": task.po_name,
                "requested_by": requested_by,
                "run_id": run_id,
            },
        )
        try:
            reply = await self._agents.for_name(AGENT).send(
                task.model_dump_json(), case_id=case.case_id
            )
        except (ScError, LookupError) as exc:
            error = exc if isinstance(exc, ScError) else ScError(str(exc))
            outcome = outcome_from_reply(case, AGENT, task.kind, task.case_id, None, error=error)
        else:
            outcome = outcome_from_reply(case, AGENT, task.kind, task.case_id, reply)
        update = await consolidate_outcome(
            self._cases, self._escalator, self._conversations, outcome
        )
        logger.bind(case_id=case.case_id, kind=task.kind, status=update.status).info(
            "sourcing task dispatched"
        )
        return {
            "case_id": case.case_id,
            "case_code": case.code,
            "thread_id": task.case_id,
            "status": outcome.status,
            "summary": outcome.summary,
            "approval_id": outcome.approval_id,
        }


__all__ = ["AGENT", "HttpSourcingSource", "SourcingDispatcher", "SourcingSource"]
