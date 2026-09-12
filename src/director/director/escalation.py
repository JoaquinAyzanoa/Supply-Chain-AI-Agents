"""Handing a case to a human.

``Escalator.escalate`` is called by the workflow when an agent fails or
returns ``escalated``, when the router decides no agent can act, and by the
follow-up job when a policy limit is reached. The Odoo implementation asks
the model for a three-sentence summary (the only model call in this
service), creates an ``sc.approval`` of kind ``escalation`` on the order
with that summary, the reason and the trace link, and schedules a To-Do
for the approver. The in-memory one records the call for tests.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from director.store import Case, CaseStore
from sc_core.infra import tracing
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.client import ChatCompleter, system, user
from sc_core.odoo.repositories import ActivityRepo, ApprovalRepo, PurchaseOrderRepo
from sc_core.prompts import get_prompt
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.time import local_today

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
AGENT_NAME = "director"
SUMMARY_MAX_CHARS = 600


class Escalation(StrictModel):
    summary: str
    approval_id: int | None = None
    trace_url: str | None = None


@runtime_checkable
class Escalator(Protocol):
    async def escalate(
        self, case: Case, *, reason: str, details: dict[str, Any] | None = None
    ) -> Escalation: ...


class MemoryEscalator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def escalate(
        self, case: Case, *, reason: str, details: dict[str, Any] | None = None
    ) -> Escalation:
        self.calls.append({"case_id": case.case_id, "reason": reason, "details": details or {}})
        return Escalation(summary=reason, approval_id=None)


class LoggingEscalator:
    """When Odoo is not configured: make the hand-off visible in the logs."""

    async def escalate(
        self, case: Case, *, reason: str, details: dict[str, Any] | None = None
    ) -> Escalation:
        logger.bind(case_id=case.case_id, po_name=case.po_name).warning(
            "case needs a human: {}", reason
        )
        return Escalation(summary=reason)


# --- Odoo -----------------------------------------------------------------------------


@runtime_checkable
class EscalationPorts(Protocol):
    """What the Odoo escalator needs from Odoo, small enough to fake."""

    async def po_id_for(self, po_name: str) -> int | None: ...

    async def create_escalation(
        self, *, summary: str, payload: dict[str, Any], case_id: str, po_id: int | None
    ) -> int: ...

    async def schedule_review(
        self, *, res_model: str, res_id: int, summary: str, note_html: str, days: int
    ) -> None: ...


class OdooEscalationPorts:
    def __init__(
        self,
        approvals: ApprovalRepo,
        activities: ActivityRepo,
        orders: PurchaseOrderRepo,
        *,
        approver_user_id: int,
    ) -> None:
        self._approvals = approvals
        self._activities = activities
        self._orders = orders
        self._approver = approver_user_id

    async def po_id_for(self, po_name: str) -> int | None:
        po = await self._orders.get_by_name(po_name)
        return po.id if po else None

    async def create_escalation(
        self, *, summary: str, payload: dict[str, Any], case_id: str, po_id: int | None
    ) -> int:
        approval = await self._approvals.create(
            kind="escalation",
            summary=summary,
            payload=payload,
            requested_by=AGENT_NAME,
            case_id=case_id,
            po_id=po_id,
            thread_id=case_id,
        )
        return approval.id

    async def schedule_review(
        self, *, res_model: str, res_id: int, summary: str, note_html: str, days: int
    ) -> None:
        await self._activities.create_approval(
            res_model=res_model,
            res_id=res_id,
            user_id=self._approver,
            summary=summary,
            note_html=note_html,
            deadline=local_today() + timedelta(days=days),
        )


class OdooApprovals:
    """``ApprovalsPort`` over the Odoo repositories (pending list, reminder note, expiry)."""

    def __init__(self, approvals: ApprovalRepo, orders: PurchaseOrderRepo) -> None:
        self._approvals = approvals
        self._orders = orders

    async def pending(self) -> list[Any]:
        return await self._approvals.pending()

    async def remind(self, approval: Any, *, days_pending: int) -> None:
        if approval.po_id is None:
            return
        await self._orders.post_note(
            approval.po_id.id,
            f"<p>Recordatorio: la aprobación #{approval.id} ({approval.kind}) lleva "
            f"{days_pending} días pendiente: {approval.summary}</p>",
        )

    async def expire(self, approval_id: int, *, reason: str) -> None:
        await self._approvals.expire(approval_id, reason=reason)


class OdooEscalator:
    def __init__(
        self,
        chat: ChatCompleter,
        ports: EscalationPorts,
        cases: CaseStore,
        *,
        deadline_days: int = 2,
        langfuse: LangfuseCfg | None = None,
    ) -> None:
        self._chat = chat
        self._ports = ports
        self._cases = cases
        self._deadline_days = deadline_days
        self._langfuse = langfuse

    async def escalate(
        self, case: Case, *, reason: str, details: dict[str, Any] | None = None
    ) -> Escalation:
        details = details or {}
        history = await self._history(case)
        summary = await self.summarize(case, reason=reason, details=details, history=history)
        trace_url = tracing.trace_url(case.trace_id)
        po_id = await self._ports.po_id_for(case.po_name) if case.po_name else None
        payload = {
            "reason": reason,
            "details": details,
            "case_id": case.case_id,
            "case_kind": case.kind,
            "po_name": case.po_name,
            "trace_url": trace_url,
            "history": history,
        }
        approval_id = await self._ports.create_escalation(
            summary=summary, payload=payload, case_id=case.case_id, po_id=po_id
        )
        note = f"<p>{summary}</p><p>Motivo: {reason}</p>"
        if trace_url:
            note += f'<p><a href="{trace_url}">Traza en Langfuse</a></p>'
        res_model, res_id = ("purchase.order", po_id) if po_id else ("sc.approval", approval_id)
        try:
            await self._ports.schedule_review(
                res_model=res_model,
                res_id=res_id,
                summary=f"Escalación {case.po_name or case.kind}",
                note_html=note,
                days=self._deadline_days,
            )
        except ScError as exc:  # the approval exists; a missing To-Do is not worth failing for
            logger.bind(case_id=case.case_id).warning("review activity not scheduled: {}", exc)
        logger.bind(case_id=case.case_id, approval_id=approval_id, po_name=case.po_name).info(
            "case escalated"
        )
        return Escalation(summary=summary, approval_id=approval_id, trace_url=trace_url)

    async def summarize(
        self, case: Case, *, reason: str, details: dict[str, Any], history: list[str]
    ) -> str:
        """Three sentences from the model; the reason itself when the model is unavailable."""
        prompt = get_prompt("escalation_summary", local_dir=PROMPTS_DIR, cfg=self._langfuse)
        text = prompt.compile(
            case_kind=case.kind,
            po_name=case.po_name or "(sin orden)",
            case_status=case.status,
            reason=reason,
            details=_plain(details),
            history="\n".join(f"- {line}" for line in history) or "- (sin historial)",
        )
        try:
            result = await self._chat.complete(
                [system(text), user("Redacta el resumen.")],
                temperature=0.2,
                max_tokens=400,
                name="escalation_summary",
                metadata={"case_id": case.case_id},
            )
        except ScError as exc:
            logger.bind(case_id=case.case_id).warning("summary model call failed: {}", exc)
            return reason[:SUMMARY_MAX_CHARS]
        summary = " ".join(result.text.split()).strip()
        return summary[:SUMMARY_MAX_CHARS] if summary else reason[:SUMMARY_MAX_CHARS]

    async def _history(self, case: Case, limit: int = 8) -> list[str]:
        lines: list[str] = []
        for event in (await self._cases.events(case.case_id))[-limit:]:
            payload = event.payload
            detail = (
                payload.get("summary")
                or payload.get("text")
                or payload.get("reason")
                or payload.get("task")
                or payload.get("event_type")
                or ""
            )
            lines.append(f"{event.kind}: {detail}" if detail else event.kind)
        return lines


def _plain(details: dict[str, Any]) -> str:
    parts = [f"{k}={v}" for k, v in details.items() if v not in (None, "", {}, [])]
    return "; ".join(parts) or "(ninguno)"
