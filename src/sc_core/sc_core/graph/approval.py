"""The human-approval interrupt.

LangGraph re-executes a node from its start when the graph resumes, so the
side effect (creating the ``sc.approval`` in Odoo) and the pause must live in
two nodes:

1. ``<step>.request``: create the approval with the agent's callback URL
   and the shared secret, schedule a To-Do for the approver on the record,
   and store ``{approval_id, step, kind}`` in ``pending_approvals``.
2. ``<step>.await``: ``interrupt()`` with that entry. LangGraph persists the
   state and the run ends as ``awaiting_approval``. When Odoo resolves the
   approval it POSTs the decision to the callback; the agent resumes the
   thread with ``Command(resume=decision)``; ``interrupt()`` returns it and
   the decision is appended to ``approvals``.

``add_approval`` wires both nodes and a conditional edge on the decision.
Auto-approval (trusted suppliers) records the decision in step 1, so step 2
finds it and never pauses.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable
from datetime import timedelta
from typing import Any, Literal, Protocol

from langgraph.graph import StateGraph
from langgraph.types import interrupt
from loguru import logger
from pydantic import Field

from sc_core.odoo.models import ApprovalKind
from sc_core.odoo.repositories import ActivityRepo, ApprovalRepo
from sc_core.schema.base import StrictModel
from sc_core.shared.time import local_today

Decision = Literal["approved", "rejected", "expired"]


class ApprovalRequest(StrictModel):
    kind: ApprovalKind
    summary: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any]
    res_model: str = "purchase.order"
    res_id: int | None = None
    po_id: int | None = None
    auto_approve: bool = False
    auto_reason: str | None = None
    review_on_approval: bool = False
    """Schedule the approver's To-Do on the approval itself (records without an activity mixin)."""


class ApprovalDecision(StrictModel):
    approval_id: int
    status: Decision
    step: str
    kind: str | None = None
    resolved_by: str | None = None
    reason: str | None = None
    details: dict[str, Any] | None = None  # per-line acceptance and edits (planning runs)

    @property
    def approved(self) -> bool:
        return self.status == "approved"


class ApprovalPorts(Protocol):
    async def create_approval(
        self,
        *,
        kind: ApprovalKind,
        summary: str,
        payload: dict[str, Any],
        requested_by: str,
        case_id: str,
        run_id: str | None,
        po_id: int | None,
        res_model: str | None,
        res_id: int | None,
        callback_url: str | None,
        callback_secret: str | None,
    ) -> int: ...

    async def schedule_review(
        self, *, res_model: str, res_id: int, user_id: int, summary: str, note_html: str, days: int
    ) -> None: ...


class OdooApprovalPorts:
    def __init__(self, approvals: ApprovalRepo, activities: ActivityRepo) -> None:
        self._approvals = approvals
        self._activities = activities

    async def create_approval(self, **kwargs: Any) -> int:
        approval = await self._approvals.create(**kwargs)
        return approval.id

    async def schedule_review(
        self, *, res_model: str, res_id: int, user_id: int, summary: str, note_html: str, days: int
    ) -> None:
        await self._activities.create_approval(
            res_model=res_model,
            res_id=res_id,
            user_id=user_id,
            summary=summary,
            note_html=note_html,
            deadline=local_today() + timedelta(days=days),
        )


RequestBuilder = Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]


class ApprovalGateway:
    def __init__(
        self,
        ports: ApprovalPorts,
        *,
        agent_name: str,
        callback_url: str | None,
        callback_secret: str | None,
        approver_user_id: int,
        deadline_days: int,
    ) -> None:
        self._ports = ports
        self._agent = agent_name
        self._callback_url = callback_url
        self._callback_secret = callback_secret
        self._approver = approver_user_id
        self._deadline_days = deadline_days

    # --- node 1 -------------------------------------------------------------------

    async def prepare(
        self, state: dict[str, Any], step: str, req: ApprovalRequest
    ) -> dict[str, Any]:
        """Create the approval (or record an automatic decision). Idempotent per step."""
        if decision_for(state, step) is not None or pending_for(state, step) is not None:
            return {}
        if req.auto_approve:
            decision = ApprovalDecision(
                approval_id=0,
                status="approved",
                step=step,
                resolved_by="auto",
                reason=req.auto_reason,
            )
            logger.bind(step=step).info("approval skipped: {}", req.auto_reason or "auto")
            return {"approvals": [decision.model_dump()]}
        res_id = req.res_id if req.res_id is not None else req.po_id
        approval_id = await self._ports.create_approval(
            kind=req.kind,
            summary=req.summary,
            payload={**req.payload, "step": step},
            requested_by=self._agent,
            case_id=state["case_id"],
            run_id=state.get("run_id"),
            po_id=req.po_id,
            res_model=req.res_model if res_id is not None else None,
            res_id=res_id,
            callback_url=self._callback_url,
            callback_secret=self._callback_secret,
        )
        if req.review_on_approval:
            review_model, review_id = "sc.approval", approval_id
        else:
            review_model, review_id = req.res_model, res_id or 0
        if review_id:
            await self._ports.schedule_review(
                res_model=review_model,
                res_id=review_id,
                user_id=self._approver,
                summary=f"AI agent ({self._agent}): {req.summary}",
                note_html=render_note(req),
                days=self._deadline_days,
            )
        logger.bind(approval_id=approval_id, kind=req.kind, step=step).info("approval requested")
        return {"pending_approvals": [{"approval_id": approval_id, "step": step, "kind": req.kind}]}

    # --- node 2 -------------------------------------------------------------------

    def await_decision(self, state: dict[str, Any], step: str) -> dict[str, Any]:
        """Pause until the decision for ``step`` arrives; no-op when it already did."""
        if decision_for(state, step) is not None:
            return {}
        pending = pending_for(state, step)
        if pending is None:
            raise RuntimeError(f"no approval was prepared for step {step!r}")
        raw = interrupt(pending)
        decision = ApprovalDecision.model_validate({**pending, **dict(raw)})
        logger.bind(approval_id=decision.approval_id, step=step).info(
            "approval {}", decision.status
        )
        return {"approvals": [decision.model_dump()]}

    # --- wiring -------------------------------------------------------------------

    def add_approval(
        self,
        graph: StateGraph,
        *,
        step: str,
        build: RequestBuilder,
        after: str | None,
        approved: str,
        rejected: str,
    ) -> str:
        """Add ``<step>.request`` -> ``<step>.await`` after ``after``; branch on the decision.

        ``after=None`` adds no incoming edge: the caller routes to
        ``<step>.request`` itself (a conditional edge, for instance).
        """
        request_node, await_node = f"{step}.request", f"{step}.await"

        async def request(state: Any) -> dict[str, Any]:
            return await self.prepare(state, step, await build(state))

        def wait(state: Any) -> dict[str, Any]:
            return self.await_decision(state, step)

        graph.add_node(request_node, request)
        graph.add_node(await_node, wait)
        if after is not None:
            graph.add_edge(after, request_node)
        graph.add_edge(request_node, await_node)
        graph.add_conditional_edges(
            await_node, decided(step), {"approved": approved, "rejected": rejected}
        )
        return await_node


def decided(step: str) -> Callable[[dict[str, Any]], Hashable]:
    def route(state: dict[str, Any]) -> Hashable:
        decision = decision_for(state, step)
        return "approved" if decision is not None and decision.approved else "rejected"

    return route


def decision_for(state: dict[str, Any], step: str) -> ApprovalDecision | None:
    for entry in reversed(state.get("approvals") or []):
        if entry.get("step") == step:
            return ApprovalDecision.model_validate(entry)
    return None


def pending_for(state: dict[str, Any], step: str) -> dict[str, Any] | None:
    for entry in state.get("pending_approvals") or []:
        if entry.get("step") == step:
            return dict(entry)
    return None


def render_note(req: ApprovalRequest) -> str:
    """Human-readable HTML for the Odoo activity: the summary and the payload as a list."""
    items = "".join(
        f"<li><b>{_esc(str(k))}</b>: {_esc(_short(v))}</li>" for k, v in req.payload.items()
    )
    return f"<p>{_esc(req.summary)}</p><ul>{items}</ul>"


def _short(value: Any, limit: int = 300) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
