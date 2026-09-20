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
The autonomy policy (``RuntimeSettings.autonomy``) is consulted in step 1 with
the request's facts: an automatic level records the decision there, writes an
``auto_actions`` row, and step 2 finds the decision and never pauses.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from langgraph.graph import StateGraph
from langgraph.types import interrupt
from loguru import logger
from pydantic import Field

from sc_core.graph.auto_actions import AutoActionPorts, AutoActionRecord
from sc_core.graph.reasoning import build_reasoning
from sc_core.i18n import Language, t
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.odoo.models import ApprovalKind
from sc_core.odoo.repositories import ActivityRepo, ApprovalRepo
from sc_core.schema.autonomy import ActionFacts, AutonomyPolicy, PolicyDecision, Reasoning
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
    facts: ActionFacts | None = None
    """What the autonomy policy may test; stored in the payload for the preview."""
    force_approval: bool = False
    """A person asked to read this one first: no rule applies."""
    revert: dict[str, Any] | None = None
    """The inverse write when the action runs alone (per kind), or None: not revertible."""
    reasoning: Reasoning | None = None
    """The node's own reasons (facts in words, alternatives); the gateway completes them."""


class ApprovalDecision(StrictModel):
    approval_id: int
    status: Decision
    step: str
    kind: str | None = None
    resolved_by: str | None = None
    reason: str | None = None
    details: dict[str, Any] | None = None  # per-line acceptance and edits (planning runs)
    rule_id: str | None = None  # the autonomy rule that decided, when no person did
    level: str | None = None  # auto | auto_notice

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
        language: Language = "en",
        control_tower_url: str | None = None,
        policy: Callable[[], Awaitable[AutonomyPolicy]] | None = None,
        auto_actions: AutoActionPorts | None = None,
    ) -> None:
        self._ports = ports
        self._policy = policy
        self._auto_actions = auto_actions
        self._agent = agent_name
        self._callback_url = callback_url
        self._callback_secret = callback_secret
        self._approver = approver_user_id
        self._deadline_days = deadline_days
        self._language = language
        self._control_tower_url = control_tower_url

    # --- node 1 -------------------------------------------------------------------

    async def prepare(
        self, state: dict[str, Any], step: str, req: ApprovalRequest
    ) -> dict[str, Any]:
        """Create the approval (or record an automatic decision). Idempotent per step."""
        if decision_for(state, step) is not None or pending_for(state, step) is not None:
            return {}
        verdict = await self._verdict(req)
        automatic = await self._automatic(state, step, req, verdict)
        if automatic is not None:
            return {"approvals": [automatic.model_dump()]}
        res_id = req.res_id if req.res_id is not None else req.po_id
        payload = {**req.payload, "step": step}
        if req.facts is not None:
            payload["facts"] = req.facts.model_dump(mode="json")  # replayed by the preview
        payload["reasoning"] = self._reasoning(req, verdict, "approve").model_dump(mode="json")
        approval_id = await self._ports.create_approval(
            kind=req.kind,
            summary=req.summary,
            payload=payload,
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
                summary=t("approval.todo", self._language, summary=req.summary)[:200],
                note_html=render_note(
                    req, language=self._language, control_tower_url=self._control_tower_url
                ),
                days=self._deadline_days,
            )
        logger.bind(approval_id=approval_id, kind=req.kind, step=step).info("approval requested")
        return {"pending_approvals": [{"approval_id": approval_id, "step": step, "kind": req.kind}]}

    async def _verdict(self, req: ApprovalRequest) -> PolicyDecision | None:
        """What the autonomy policy says about the request; None when none is in force."""
        if self._policy is None or req.force_approval:
            return None
        return (await self._policy()).decide(req.kind, req.facts)

    def _reasoning(
        self, req: ApprovalRequest, verdict: PolicyDecision | None, level: str
    ) -> Reasoning:
        return build_reasoning(
            kind=req.kind,
            facts=req.facts,
            given=req.reasoning,
            verdict=verdict,
            level=level,
            forced=req.force_approval,
            lang=self._language,
        )

    async def _automatic(
        self,
        state: dict[str, Any],
        step: str,
        req: ApprovalRequest,
        verdict: PolicyDecision | None = None,
    ) -> ApprovalDecision | None:
        """The decision when no person is needed: the request says so (legacy
        ``auto_approve``) or the autonomy policy lets it through."""
        level, rule_id, reason = "approve", None, req.auto_reason
        if req.auto_approve:
            level = "auto_notice"
        elif verdict is not None:
            level, rule_id, reason = verdict.level, verdict.rule_id, verdict.reason
        if level == "approve":
            return None
        who = f"policy:{rule_id}" if rule_id else "auto"
        decision = ApprovalDecision(
            approval_id=0,
            status="approved",
            step=step,
            kind=req.kind,
            resolved_by=who,
            reason=reason,
            rule_id=rule_id,
            level=level,
        )
        if self._auto_actions is not None:
            hours = 24
            if rule_id and self._policy is not None:
                rule = (await self._policy()).rule(rule_id)
                hours = rule.revert_hours if rule else hours
            revertible = level == "auto_notice" and req.revert is not None
            await self._auto_actions.record(
                AutoActionRecord(
                    case_id=state.get("case_id"),
                    run_id=state.get("run_id"),
                    agent=self._agent,
                    kind=req.kind,
                    level=level,
                    rule_id=rule_id,
                    summary=req.summary,
                    po_id=req.po_id,
                    po_name=str(req.payload.get("po_name") or "") or None,
                    partner_id=req.facts.partner_id if req.facts else None,
                    payload={
                        **{k: v for k, v in req.payload.items() if k != "html_body"},
                        "reasoning": self._reasoning(req, verdict, level).model_dump(mode="json"),
                    },
                    revert=req.revert if revertible else None,
                    revert_until=datetime.now(UTC) + timedelta(hours=hours) if revertible else None,
                )
            )
        logger.bind(step=step, kind=req.kind, level=level, rule=rule_id).info(
            "approval not needed: {}", reason or "auto"
        )
        return decision

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


def policy_from(reader: RuntimeSettingsReader) -> Callable[[], Awaitable[AutonomyPolicy]]:
    """The current autonomy policy from a ``RuntimeSettingsReader`` (a minute of cache)."""

    async def current() -> AutonomyPolicy:
        settings = await reader.current()
        return settings.autonomy

    return current


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


def render_note(
    req: ApprovalRequest, *, language: Language = "en", control_tower_url: str | None = None
) -> str:
    """The approver's To-Do note (see :mod:`sc_core.graph.notes`)."""
    from sc_core.graph.notes import render_note as _render

    return _render(req, language=language, control_tower_url=control_tower_url)
