"""Talk to the director about one case.

``POST /api/cases/{ref}/chat`` records the person's message on the case
and answers with the model, which only sees what the case, the order, the
policy and the pending approvals say. When the message is an instruction
the director maps it to one of a few actions (ask the supplier for a
date, send a reminder, hold the case until a date, close the case) and
describes it; nothing runs until an approver confirms it
(``POST .../chat/{message_id}/confirm``). Every message, confirmation and
outcome is a case event, so the timeline and Odoo keep the conversation.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.approvals import ApprovalsGateway
from director.api.auth import Approver, Principal, Viewer
from director.api.cases import load_case
from director.api.exceptions import ExceptionsSource
from director.escalation import PROMPTS_DIR
from director.store import Case, CaseEvent, CaseStore
from director.workflow import Deps, consolidate_outcome, outcome_from_reply
from sc_core.i18n import Language, language_name
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.client import ChatCompleter, assistant, system, user
from sc_core.llm.structured import StructuredOutputFailed, complete_structured
from sc_core.odoo.models import PurchaseOrder
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.idempotency import new_id
from sc_core.shared.time import local_today, utc_now

router = APIRouter(prefix="/cases", tags=["chat"])

ActionKind = Literal["request_eta", "follow_up", "hold_until", "close_case", "link_email"]
ORDER_ACTIONS: frozenset[str] = frozenset({"request_eta", "follow_up"})


class ProposedAction(StrictModel):
    kind: ActionKind
    note: str | None = Field(
        default=None, max_length=1000, description="what to stress, or the decision to record"
    )
    until: date | None = Field(default=None, description="hold_until: look at the case again then")
    po_name: str | None = Field(default=None, max_length=32, description="link_email: the order")
    explanation: str = Field(min_length=1, max_length=500, description="what will happen")


class AssistantReply(StrictModel):
    reply: str = Field(min_length=1, max_length=2000)
    action: ProposedAction | None = None


class ChatMessage(StrictModel):
    id: int
    at: datetime
    role: Literal["user", "director"]
    text: str
    by: str | None = None
    action: ProposedAction | None = None
    action_status: Literal["proposed", "confirmed", "dismissed"] | None = None


class ChatRequest(StrictModel):
    text: str = Field(min_length=1, max_length=2000)


class ChatTurn(StrictModel):
    messages: list[ChatMessage]


# --- the assistant --------------------------------------------------------------------------


@runtime_checkable
class OrderLookup(Protocol):
    async def by_names(self, names: Sequence[str]) -> list[PurchaseOrder]: ...


class NoOrders:
    async def by_names(self, names: Sequence[str]) -> list[PurchaseOrder]:
        return []


class CaseAssistant:
    """Answers about a case from its recorded facts; proposes actions, never runs them."""

    def __init__(
        self,
        chat: ChatCompleter,
        *,
        cases: CaseStore,
        approvals: ApprovalsGateway,
        orders: OrderLookup | None = None,
        policy: ExceptionsSource | None = None,
        language: Language = "en",
        langfuse: LangfuseCfg | None = None,
    ) -> None:
        self._chat = chat
        self._cases = cases
        self._approvals = approvals
        self._orders = orders or NoOrders()
        self._policy = policy
        self._language = language
        self._langfuse = langfuse

    async def answer(
        self, case: Case, question: str, *, history: Sequence[ChatMessage]
    ) -> AssistantReply:
        prompt = get_prompt("case_assistant", local_dir=PROMPTS_DIR, cfg=self._langfuse)
        text = prompt.compile(
            language=language_name(self._language),
            today=local_today().isoformat(),
            case=self._describe_case(case),
            order=await self._describe_order(case),
            email=describe_email(await self._cases.events(case.case_id)),
            policy=await self._describe_policy(),
            approvals=await self._describe_approvals(case),
            timeline=self._describe_timeline(await self._cases.events(case.case_id)),
        )
        messages = [system(text)]
        for turn in history[-10:]:
            messages.append(user(turn.text) if turn.role == "user" else assistant(turn.text))
        messages.append(user(question))
        reply = await complete_structured(
            self._chat,
            messages,
            AssistantReply,
            name="case_assistant",
            metadata={"case_id": case.case_id},
        )
        return _sanitize(reply, case, await self._cases.events(case.case_id))

    def _describe_case(self, case: Case) -> str:
        parts = [f"{case.code}, kind {case.kind}, status {case.status}"]
        if case.po_name:
            parts.append(f"order {case.po_name}")
        if case.summary:
            parts.append(f"last summary: {case.summary}")
        if case.next_action_at:
            parts.append(f"on hold until {case.next_action_at.date().isoformat()}")
        return "; ".join(parts)

    async def _describe_order(self, case: Case) -> str:
        if not case.po_name:
            return "(no order on this case)"
        try:
            orders = await self._orders.by_names([case.po_name])
        except ScError as exc:
            logger.warning("order lookup failed for the assistant: {}", exc)
            return f"{case.po_name} (Odoo did not answer)"
        if not orders:
            return f"{case.po_name} (not found in Odoo)"
        po = orders[0]
        planned = po.date_planned.date().isoformat() if po.date_planned else "unknown"
        return (
            f"{po.name} with {po.partner_id.name}: state {po.state}, planned delivery {planned}, "
            f"receipt {po.receipt_status or 'none'}, total {po.amount_total:.2f} "
            f"{po.currency_id.name if po.currency_id else ''}".strip()
        )

    async def _describe_policy(self) -> str:
        if self._policy is None:
            return "(default policy)"
        policy = await self._policy.effective_policy()
        late = policy.po_late_days
        ask_after = late[0] if late else "?"
        escalate_after = late[1] if len(late) > 1 else "?"
        return (
            f"RFQ reminders after {policy.rfq_no_reply_days} days of silence, then a person; "
            f"confirmed orders: ask for the date {policy.po_eta_request_before_days} days before, "
            f"request a new ETA {ask_after} days late, escalate {escalate_after} days late"
        )

    async def _describe_approvals(self, case: Case) -> str:
        pending = await self._approvals.list(status="pending", kind=None, po_name=case.po_name)
        mine = [a for a in pending if a.thread_id == case.case_id or a.case_id == case.case_id]
        if not mine and case.po_name:
            mine = pending
        return "; ".join(f"#{a.id} {a.kind}: {a.summary}" for a in mine) or "(none)"

    @staticmethod
    def _describe_timeline(events: Sequence[CaseEvent], limit: int = 20) -> str:
        lines = []
        for event in [e for e in events if e.kind != "chat"][-limit:]:
            p = event.payload
            when = event.at.strftime("%Y-%m-%d %H:%M")
            detail = (
                (f"rule {p['rule']}: {p.get('reason', '')}" if p.get("rule") else None)
                or p.get("summary")
                or p.get("text")
                or p.get("reason")
                or (f"{p.get('task')} sent to {p.get('agent')}" if p.get("task") else None)
                or p.get("event_type")
                or ""
            )
            status = f" [{p['status']}]" if p.get("status") else ""
            lines.append(f"- {when} {event.kind}{status}: {detail}".rstrip(": "))
        return "\n".join(lines) or "- (nothing yet)"


def _sanitize(reply: AssistantReply, case: Case, events: Sequence[CaseEvent]) -> AssistantReply:
    """Drop actions the case cannot take (an email about no order, a hold without a date)."""
    action = reply.action
    if action is None:
        return reply
    if action.kind in ORDER_ACTIONS and not case.po_name:
        return reply.model_copy(update={"action": None})
    if action.kind == "hold_until" and action.until is None:
        return reply.model_copy(update={"action": None})
    if action.kind == "link_email" and (not action.po_name or unlinked_message(events) is None):
        return reply.model_copy(update={"action": None})
    return reply


def unlinked_message(events: Sequence[CaseEvent]) -> str | None:
    """The Graph id of the email this case is about, when the case has one."""
    for event in reversed(events):
        if event.kind in ("event_received", "escalated"):
            message_id = event.payload.get("graph_message_id") or (
                event.payload.get("details") or {}
            ).get("graph_message_id")
            if message_id:
                return str(message_id)
    return None


def describe_email(events: Sequence[CaseEvent]) -> str:
    facts = next(
        (
            e.payload
            for e in reversed(events)
            if e.kind == "event_received" and e.payload.get("graph_message_id")
        ),
        None,
    )
    if not facts:
        return "(no email on this case)"
    sender = facts.get("sender_address") or "unknown sender"
    link = (
        "a person can open it in Outlook from the case"
        if facts.get("web_link")
        else "no Outlook link"
    )
    attachments = "with attachments" if facts.get("has_attachments") else "no attachments"
    return f"from {sender}, {attachments}, {link}; its text is not stored here"


# --- the actions ----------------------------------------------------------------------------


class ChatActions:
    """Runs a confirmed action and says what happened, in one sentence."""

    def __init__(self, deps: Deps, approvals: ApprovalsGateway) -> None:
        self._deps = deps
        self._approvals = approvals

    async def execute(self, case: Case, action: ProposedAction, *, by: Principal) -> str:
        if action.kind in ORDER_ACTIONS:
            return await self._send_task(case, action, by)
        if action.kind == "hold_until":
            assert action.until is not None
            until = datetime.combine(action.until, time.min, tzinfo=UTC)
            await self._deps.cases.update(case.case_id, next_action_at=until)
            await self._deps.cases.add_event(
                case.case_id,
                "note",
                {
                    "text": (
                        f"On hold until {action.until.isoformat()} by {by.name}: "
                        f"{action.note or action.explanation}"
                    )
                },
            )
            return (
                f"On hold until {action.until.isoformat()}; automatic follow-ups pause until then."
            )
        if action.kind == "close_case":
            return await self._close(case, action, by)
        if action.kind == "link_email":
            return await self._link_email(case, action, by)
        raise HTTPException(status_code=422, detail=f"unknown action {action.kind}")

    async def _link_email(self, case: Case, action: ProposedAction, by: Principal) -> str:
        assert action.po_name is not None
        message_id = unlinked_message(await self._deps.cases.events(case.case_id))
        if message_id is None:
            raise HTTPException(status_code=409, detail="this case has no email to link")
        po_name = action.po_name.strip().upper()
        thread_id = f"chat_{new_id('t')}"
        task = SupplierCommsTask(
            kind="resolve_unlinked",
            case_id=thread_id,
            graph_message_id=message_id,
            candidate_po_names=[po_name],
            assigned_po_name=po_name,
            notes=f"{by.name} assigned this email to {po_name}",
        )
        await self._deps.cases.update(case.case_id, po_name=po_name)
        await self._deps.cases.add_event(
            case.case_id,
            "task_sent",
            {
                "agent": "supplier_comms",
                "task": task.kind,
                "thread_id": thread_id,
                "po_name": po_name,
                "by": by.email,
            },
        )
        try:
            reply = await self._deps.agents.for_name("supplier_comms").send(
                task.model_dump_json(), case_id=case.case_id
            )
        except ScError as exc:
            outcome = outcome_from_reply(
                case, "supplier_comms", task.kind, thread_id, None, error=exc
            )
        else:
            outcome = outcome_from_reply(case, "supplier_comms", task.kind, thread_id, reply)
        await consolidate_outcome(
            self._deps.cases, self._deps.escalator, self._deps.conversations, outcome
        )
        return f"Email linked to {po_name}: {outcome.summary}"

    async def _send_task(self, case: Case, action: ProposedAction, by: Principal) -> str:
        assert case.po_name is not None
        thread_id = f"chat_{new_id('t')}"
        task = SupplierCommsTask(
            kind=action.kind,  # type: ignore[arg-type]
            case_id=thread_id,
            po_name=case.po_name,
            notes=f"{by.name} asked: {action.note or action.explanation}",
        )
        await self._deps.cases.add_event(
            case.case_id,
            "task_sent",
            {
                "agent": "supplier_comms",
                "task": task.kind,
                "thread_id": thread_id,
                "po_name": case.po_name,
                "by": by.email,
            },
        )
        try:
            reply = await self._deps.agents.for_name("supplier_comms").send(
                task.model_dump_json(), case_id=case.case_id
            )
        except ScError as exc:
            outcome = outcome_from_reply(
                case, "supplier_comms", task.kind, thread_id, None, error=exc
            )
        else:
            outcome = outcome_from_reply(case, "supplier_comms", task.kind, thread_id, reply)
        await consolidate_outcome(
            self._deps.cases, self._deps.escalator, self._deps.conversations, outcome
        )
        return outcome.summary

    async def _close(self, case: Case, action: ProposedAction, by: Principal) -> str:
        decision = action.note or action.explanation
        pending = await self._approvals.list(status="pending", kind="escalation", po_name=None)
        closed = 0
        for approval in pending:
            if approval.thread_id == case.case_id or approval.case_id == case.case_id:
                await self._approvals.resolve(
                    approval.id, "approved", by_name=by.name, reason=decision, details=None
                )
                closed += 1
        await self._deps.cases.update(case.case_id, status="done", summary=decision[:500])
        await self._deps.cases.add_event(
            case.case_id, "note", {"text": f"Closed by {by.name}: {decision}"}
        )
        return f"Case closed{' and the pending escalation resolved' if closed else ''}: {decision}"


# --- storage on the case ------------------------------------------------------------------


def messages_of(events: Sequence[CaseEvent]) -> list[ChatMessage]:
    """The conversation, with each proposed action's status derived from later messages."""
    confirmed = {e.payload.get("confirms") for e in events if e.kind == "chat"}
    dismissed = {e.payload.get("dismisses") for e in events if e.kind == "chat"}
    out = []
    for event in events:
        if event.kind != "chat":
            continue
        p = event.payload
        action = ProposedAction.model_validate(p["action"]) if p.get("action") else None
        status = None
        if action is not None:
            status = (
                "confirmed"
                if event.id in confirmed
                else "dismissed"
                if event.id in dismissed
                else "proposed"
            )
        out.append(
            ChatMessage(
                id=event.id,
                at=event.at,
                role=p.get("role", "director"),
                text=str(p.get("text", "")),
                by=p.get("by"),
                action=action,
                action_status=status,  # type: ignore[arg-type]
            )
        )
    return out


async def _record(cases: CaseStore, case: Case, payload: dict[str, Any]) -> ChatMessage:
    event_id = await cases.add_event(case.case_id, "chat", payload)
    return next(m for m in messages_of(await cases.events(case.case_id)) if m.id == event_id)


# --- routes -----------------------------------------------------------------------------------


@router.get("/{ref}/chat", response_model=list[ChatMessage])
async def read_chat(
    ref: str,
    _: Principal = Viewer,
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
) -> list[ChatMessage]:
    case = await load_case(cases, ref)
    return messages_of(await cases.events(case.case_id))


@router.post("/{ref}/chat", response_model=ChatTurn)
async def ask(
    ref: str,
    body: ChatRequest,
    principal: Principal = Viewer,
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    assistant_: CaseAssistant = Injected(CaseAssistant),
) -> ChatTurn:
    case = await load_case(cases, ref)
    history = messages_of(await cases.events(case.case_id))
    asked = await _record(
        cases,
        case,
        {"role": "user", "text": body.text, "by": principal.email, "at": utc_now().isoformat()},
    )
    try:
        reply = await assistant_.answer(case, body.text, history=history)
    except (ScError, StructuredOutputFailed) as exc:
        logger.bind(case_id=case.case_id).warning("case assistant failed: {}", exc)
        raise HTTPException(
            status_code=502, detail="the director could not answer right now"
        ) from exc
    answered = await _record(
        cases,
        case,
        {
            "role": "director",
            "text": reply.reply,
            "action": reply.action.model_dump(mode="json") if reply.action else None,
            "replies_to": asked.id,
        },
    )
    return ChatTurn(messages=[asked, answered])


@router.post("/{ref}/chat/{message_id}/confirm", response_model=ChatTurn)
async def confirm_action(
    ref: str,
    message_id: int,
    principal: Principal = Approver,
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
    actions: ChatActions = Injected(ChatActions),
) -> ChatTurn:
    case = await load_case(cases, ref)
    message = _proposed(messages_of(await cases.events(case.case_id)), message_id)
    assert message.action is not None
    try:
        outcome = await actions.execute(case, message.action, by=principal)
    except ScError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    done = await _record(
        cases,
        case,
        {"role": "director", "text": outcome, "confirms": message_id, "by": principal.email},
    )
    logger.bind(case_id=case.case_id, action=message.action.kind, by=principal.email).info(
        "chat action confirmed"
    )
    return ChatTurn(messages=[done])


@router.post("/{ref}/chat/{message_id}/dismiss", response_model=ChatTurn)
async def dismiss_action(
    ref: str,
    message_id: int,
    principal: Principal = Approver,
    cases: CaseStore = Injected(CaseStore),  # type: ignore[type-abstract]
) -> ChatTurn:
    case = await load_case(cases, ref)
    _proposed(messages_of(await cases.events(case.case_id)), message_id)
    done = await _record(
        cases,
        case,
        {
            "role": "director",
            "text": f"Not done; dismissed by {principal.name}.",
            "dismisses": message_id,
            "by": principal.email,
        },
    )
    return ChatTurn(messages=[done])


def _proposed(messages: list[ChatMessage], message_id: int) -> ChatMessage:
    message = next((m for m in messages if m.id == message_id), None)
    if message is None or message.action is None:
        raise HTTPException(status_code=404, detail=f"message {message_id} has no action")
    if message.action_status != "proposed":
        raise HTTPException(status_code=409, detail=f"action already {message.action_status}")
    return message
