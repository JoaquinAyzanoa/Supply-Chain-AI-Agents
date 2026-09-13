"""The department assistant (phase 11 S7): one chat about the whole purchasing desk.

Where the case chat knows one case, this one reads the desk: orders at
risk, pending approvals, late and silent orders, active playbooks, the
autonomy policy, recent automatic actions, and, when the question names
them, an order, a supplier or a product. Everything it may cite is listed
with a reference; the reply carries the references it used and the API
turns them into links. An instruction becomes a plan of steps the person
confirms once; execution goes through the agents and their approvals, as
always. No model output is ever written anywhere but the conversation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol, runtime_checkable

from loguru import logger
from pydantic import BaseModel, Field

from director.api.approvals import ApprovalsGateway
from director.api.board import BoardOrders
from director.api.chat import ChatActions, ProposedAction
from director.api.exceptions import ExceptionsSource
from director.api.performance import PerformanceSource
from director.api.planning import PlanningReadStore
from director.api.risk import RiskSource
from director.autonomy import AutoActionsStore
from director.escalation import PROMPTS_DIR
from director.playbooks import PlaybookEngine
from director.policies import PoFacts
from director.sourcing import SourcingDispatcher
from director.store import CaseStore
from sc_core.i18n import Language, language_name
from sc_core.infra.db import Database
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm.client import ChatCompleter, assistant, system, user
from sc_core.llm.structured import complete_structured
from sc_core.odoo.repositories.approval import ApprovalRepo
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import SourcingTask
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.idempotency import new_id
from sc_core.shared.time import local_today, utc_now

PO_NAME = re.compile(r"\bP\d{5}\b")
CODE_LIKE = re.compile(r"\b[A-Z0-9]{2,}(?:-[A-Z0-9]+)+\b")
StepKind = Literal[
    "quote_round",
    "alternate_source",
    "request_eta",
    "follow_up",
    "hold_until",
    "close_case",
    "start_playbook",
]
ORDER_STEPS: frozenset[str] = frozenset(
    {"alternate_source", "request_eta", "follow_up", "hold_until", "close_case", "start_playbook"}
)


class Citation(StrictModel):
    ref: str
    label: str
    path: str


class PlanStep(StrictModel):
    kind: StepKind
    po_name: str | None = Field(default=None, max_length=32)
    product_ref: str | None = Field(default=None, max_length=64)
    product_id: int | None = None
    qty: float | None = Field(default=None, gt=0)
    partner_ids: list[int] = []
    deadline_days: int | None = Field(default=None, ge=1, le=60)
    until: date | None = None
    playbook: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=1000)
    explanation: str = Field(min_length=1, max_length=500)


class Plan(StrictModel):
    summary: str = Field(min_length=1, max_length=300)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)


class AssistantAnswer(BaseModel):
    """What the model returns; references are resolved by code."""

    reply: str = Field(min_length=1, max_length=3000)
    citations: list[str] = Field(default_factory=list)
    plan: Plan | None = None


class AssistantMessage(StrictModel):
    id: int
    at: datetime
    role: Literal["user", "director"]
    text: str
    by: str | None = None
    citations: list[Citation] = []
    plan: Plan | None = None
    plan_status: Literal["proposed", "confirmed", "dismissed"] | None = None
    outcome: str | None = None


@runtime_checkable
class AssistantStore(Protocol):
    async def messages(self, user_email: str, *, limit: int = 60) -> list[AssistantMessage]: ...

    async def add(
        self,
        user_email: str,
        *,
        role: str,
        text: str,
        citations: list[Citation] | None = None,
        plan: Plan | None = None,
        by: str | None = None,
    ) -> AssistantMessage: ...

    async def get(self, user_email: str, message_id: int) -> AssistantMessage | None: ...

    async def set_plan_status(
        self, message_id: int, status: str, *, outcome: str | None = None
    ) -> AssistantMessage: ...


def _message(row: dict[str, Any]) -> AssistantMessage:
    citations = row.get("citations") or []
    plan = row.get("plan")
    return AssistantMessage(
        id=int(row["id"]),
        at=row["created_at"],
        role=str(row["role"]),  # type: ignore[arg-type]
        text=str(row["text"]),
        by=row.get("by"),
        citations=[Citation.model_validate(c) for c in citations],
        plan=Plan.model_validate(plan) if plan else None,
        plan_status=row.get("plan_status"),
        outcome=row.get("outcome"),
    )


class PostgresAssistantStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def messages(self, user_email: str, *, limit: int = 60) -> list[AssistantMessage]:
        rows = await self._db.fetch_all(
            "SELECT *, user_email AS by FROM assistant_messages WHERE user_email = %s "
            "ORDER BY id DESC LIMIT %s",
            (user_email, limit),
        )
        return [_message(r) for r in reversed(rows)]

    async def add(
        self,
        user_email: str,
        *,
        role: str,
        text: str,
        citations: list[Citation] | None = None,
        plan: Plan | None = None,
        by: str | None = None,
    ) -> AssistantMessage:
        row = await self._db.fetch_one(
            "INSERT INTO assistant_messages (user_email, role, text, citations, plan, plan_status) "
            "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s) RETURNING *, %s AS by",
            (
                user_email,
                role,
                text,
                json.dumps([c.model_dump(mode="json") for c in citations or []]),
                json.dumps(plan.model_dump(mode="json")) if plan else None,
                "proposed" if plan else None,
                by,
            ),
        )
        assert row is not None
        return _message(row)

    async def get(self, user_email: str, message_id: int) -> AssistantMessage | None:
        row = await self._db.fetch_one(
            "SELECT *, user_email AS by FROM assistant_messages WHERE id = %s AND user_email = %s",
            (message_id, user_email),
        )
        return _message(row) if row else None

    async def set_plan_status(
        self, message_id: int, status: str, *, outcome: str | None = None
    ) -> AssistantMessage:
        row = await self._db.fetch_one(
            "UPDATE assistant_messages SET plan_status = %s, outcome = COALESCE(%s, outcome) "
            "WHERE id = %s RETURNING *, user_email AS by",
            (status, outcome, message_id),
        )
        assert row is not None
        return _message(row)


class MemoryAssistantStore:
    def __init__(self) -> None:
        self.rows: list[AssistantMessage] = []
        self.owners: dict[int, str] = {}

    async def messages(self, user_email: str, *, limit: int = 60) -> list[AssistantMessage]:
        mine = [m for m in self.rows if self.owners.get(m.id) == user_email]
        return mine[-limit:]

    async def add(
        self,
        user_email: str,
        *,
        role: str,
        text: str,
        citations: list[Citation] | None = None,
        plan: Plan | None = None,
        by: str | None = None,
    ) -> AssistantMessage:
        message = AssistantMessage(
            id=len(self.rows) + 1,
            at=utc_now(),
            role=role,  # type: ignore[arg-type]
            text=text,
            by=by,
            citations=list(citations or []),
            plan=plan,
            plan_status="proposed" if plan else None,
        )
        self.rows.append(message)
        self.owners[message.id] = user_email
        return message

    async def get(self, user_email: str, message_id: int) -> AssistantMessage | None:
        if self.owners.get(message_id) != user_email:
            return None
        return next((m for m in self.rows if m.id == message_id), None)

    async def set_plan_status(
        self, message_id: int, status: str, *, outcome: str | None = None
    ) -> AssistantMessage:
        for index, message in enumerate(self.rows):
            if message.id == message_id:
                updated = message.model_copy(
                    update={"plan_status": status, "outcome": outcome or message.outcome}
                )
                self.rows[index] = updated
                return updated
        raise KeyError(message_id)


# --- the assistant ----------------------------------------------------------------------------


class Context:
    """The records the model may cite, numbered by reference, and the text it reads."""

    def __init__(self) -> None:
        self.refs: dict[str, Citation] = {}
        self.blocks: list[str] = []
        self.product_ids: dict[str, int] = {}
        self.orders: set[str] = set()
        self.playbooks: set[str] = set()

    def cite(self, ref: str, label: str, path: str, *, replace: bool = False) -> str:
        if replace or ref not in self.refs:
            self.refs[ref] = Citation(ref=ref, label=label[:120], path=path)
        return f"[{ref}]"

    def block(self, title: str, lines: Sequence[str]) -> None:
        body = "\n".join(f"- {line}" for line in lines) if lines else "- (nothing)"
        self.blocks.append(f"{title}:\n{body}")

    def text(self) -> str:
        return "\n\n".join(self.blocks)


class DepartmentAssistant:
    def __init__(
        self,
        chat: ChatCompleter,
        *,
        cases: CaseStore,
        approvals: ApprovalsGateway,
        exceptions: ExceptionsSource,
        auto_actions: AutoActionsStore | None = None,
        risk: RiskSource | None = None,
        playbooks: PlaybookEngine | None = None,
        orders: BoardOrders | None = None,
        performance: PerformanceSource | None = None,
        planning: PlanningReadStore | None = None,
        runtime: RuntimeSettingsReader | None = None,
        language: Language = "en",
        langfuse: LangfuseCfg | None = None,
        today: Callable[[], date] = local_today,
    ) -> None:
        self._chat = chat
        self._cases = cases
        self._approvals = approvals
        self._exceptions = exceptions
        self._auto_actions = auto_actions
        self._risk = risk
        self._playbooks = playbooks
        self._orders = orders
        self._performance = performance
        self._planning = planning
        self._runtime = runtime
        self._language: Language = language
        self._langfuse = langfuse
        self._today = today

    async def answer(
        self, question: str, *, history: Sequence[AssistantMessage]
    ) -> tuple[AssistantAnswer, list[Citation]]:
        context = await self.context(question)
        prompt = get_prompt("department_assistant", local_dir=PROMPTS_DIR, cfg=self._langfuse)
        text = prompt.compile(
            today=self._today().isoformat(),
            language=language_name(self._language),
            context=context.text(),
        )
        messages = [system(text)]
        for turn in history[-10:]:
            messages.append(user(turn.text) if turn.role == "user" else assistant(turn.text))
        messages.append(user(question))
        answer = await complete_structured(
            self._chat, messages, AssistantAnswer, name="department_assistant"
        )
        # the model lists what it used; references it wrote in brackets count as well
        mentioned = [m for m in re.findall(r"\[([^\]]+)\]", answer.reply) if m in context.refs]
        refs = dict.fromkeys([*answer.citations, *mentioned])
        citations = [context.refs[r] for r in refs if r in context.refs]
        plan = self._sanitize(answer.plan, context)
        return answer.model_copy(update={"plan": plan}), citations

    # --- the desk, as text ------------------------------------------------------------

    async def context(self, question: str) -> Context:
        ctx = Context()
        today = self._today()
        await self._risk_block(ctx)
        await self._approvals_block(ctx)
        facts = await self._exceptions_block(ctx, today)
        await self._playbooks_block(ctx)
        await self._policy_block(ctx)
        await self._actions_block(ctx)
        await self._orders_block(ctx, question, facts)
        await self._suppliers_block(ctx, question)
        await self._products_block(ctx, question)
        return ctx

    async def _risk_block(self, ctx: Context) -> None:
        if self._risk is None:
            return
        try:
            report = await self._risk.report()
        except ScError as exc:
            logger.warning("risk radar unavailable for the assistant: {}", exc)
            ctx.block("Orders at risk", ["(the risk radar did not answer)"])
            return
        lines = []
        for p in (report.get("products") or [])[:10]:
            ref = str(p.get("product_ref") or p.get("product_id"))
            if p.get("product_id") is not None:
                ctx.product_ids[ref.upper()] = int(p["product_id"])
            late = p.get("late_po_names") or []
            opened = p.get("open_po_names") or []
            lines.append(
                f"{ctx.cite(ref, str(p.get('product_name') or ref), '/risk')} "
                f"{p.get('product_name') or ''}: position {p.get('position')}, "
                f"{round(100 * float(p.get('p_stockout_30') or 0))}% stockout odds at 30 days, "
                f"{round(100 * float(p.get('p_stockout_60') or 0))}% at 60; "
                f"open orders {', '.join(opened) or 'none'}"
                + (f"; late: {', '.join(late)}" if late else "")
                + f"; suggested quantity {p.get('suggested_qty')}"
            )
        ctx.block("Products at risk (stockout odds from the forecast and the open orders)", lines)
        suppliers = [
            f"{ctx.cite(str(s.get('partner_name')), str(s.get('partner_name')), '/risk')}: "
            f"{s.get('open_lines')} open line(s), {s.get('overdue_lines')} overdue, "
            f"exposure {float(s.get('exposure') or 0):,.0f}"
            for s in report.get("suppliers") or []
        ]
        ctx.block("Suppliers with open supply", suppliers)

    async def _approvals_block(self, ctx: Context) -> None:
        pending = await self._approvals.list(status="pending", kind=None, po_name=None)
        lines = []
        for a in pending[:20]:
            payload = ApprovalRepo.payload_of(a)
            amount = (payload.get("facts") or {}).get("amount")
            po = a.po_id.name if a.po_id else None
            if po:
                ctx.orders.add(po)
            lines.append(
                f"{ctx.cite(f'#{a.id}', a.summary, f'/approvals?id={a.id}')} {a.kind}: {a.summary}"
                + (f" (order {po})" if po else "")
                + (f", amount {float(amount):,.0f}" if amount else "")
            )
        ctx.block("Pending approvals (a person decides these)", lines)

    async def _exceptions_block(self, ctx: Context, today: date) -> list[PoFacts]:
        facts = await self._exceptions.gather(today)
        lines = []
        for f in facts:
            late = (today - f.date_planned).days if f.date_planned and f.date_planned < today else 0
            silent = f.silent_days(today)
            if f.is_confirmed_open and late > 0:
                lines.append(
                    f"{ctx.cite(f.po_name, f.po_name, f'/?po={f.po_name}')} confirmed order "
                    f"{late} day(s) late (planned {f.date_planned})"
                    + (", a person has it" if f.awaiting_human else "")
                )
            elif f.is_rfq and silent:
                lines.append(
                    f"{ctx.cite(f.po_name, f.po_name, f'/?po={f.po_name}')} quotation request "
                    f"without a reply for {silent} day(s)"
                )
        ctx.block("Late and silent orders", lines)
        return facts

    async def _playbooks_block(self, ctx: Context) -> None:
        if self._playbooks is None:
            return
        lines = []
        counts = await self._playbooks.counts()
        for name, playbook in self._playbooks.playbooks.items():
            ctx.playbooks.add(name)
            active = sum(counts.get(name, {}).values())
            lines.append(
                f"{ctx.cite(name, playbook.title, '/playbooks')} {playbook.title}: "
                f"{active} active run(s); steps {', '.join(s.id for s in playbook.steps)}"
            )
        ctx.block("Playbooks (plans in code a person may start on an order)", lines)

    async def _policy_block(self, ctx: Context) -> None:
        if self._runtime is None:
            return
        settings = await self._runtime.current()
        lines = []
        for rule in settings.autonomy.rules:
            state = "on" if rule.enabled else "off"
            lines.append(
                f"{ctx.cite('policy', 'Autonomy policy', '/autonomy')} rule {rule.id} ({state}): "
                f"{rule.kind} at level {rule.level} when {rule.when.describe() or 'always'}"
            )
        if not lines:
            ctx.cite("policy", "Autonomy policy", "/autonomy")
            lines.append("[policy] no automatic rule: every action waits for a person")
        ctx.block("Autonomy policy", lines)

    async def _actions_block(self, ctx: Context) -> None:
        if self._auto_actions is None:
            return
        since = utc_now() - timedelta(days=2)
        actions = await self._auto_actions.recent(since=since, limit=10)
        lines = [
            f"{ctx.cite('policy', 'Autonomy policy', '/autonomy')} {a.created_at:%Y-%m-%d %H:%M} "
            f"{a.summary} (rule {a.rule_id or 'auto'}"
            + (f", order {a.po_name}" if a.po_name else "")
            + ")"
            for a in actions
        ]
        ctx.block("Ran alone in the last two days", lines)

    async def _orders_block(self, ctx: Context, question: str, facts: list[PoFacts]) -> None:
        names = sorted(set(PO_NAME.findall(question.upper())))
        if not names or self._orders is None:
            return
        try:
            orders = await self._orders.by_names(names)
        except ScError as exc:
            logger.warning("order lookup failed for the assistant: {}", exc)
            ctx.block("Orders named in the question", ["(Odoo did not answer)"])
            return
        lines = []
        for po in orders:
            ctx.orders.add(po.name)
            planned = po.date_planned.date().isoformat() if po.date_planned else "unknown"
            currency = po.currency_id.name if po.currency_id else ""
            line = (
                f"{ctx.cite(po.name, po.name, f'/?po={po.name}')} with {po.partner_id.name}: "
                f"state {po.state}, planned {planned}, receipt {po.receipt_status or 'none'}, "
                f"total {po.amount_total:,.2f} {currency}".rstrip()
            )
            open_cases = await self._cases.open_for_po(po.name)
            if open_cases:
                case = open_cases[0]
                line += f"; open case {ctx.cite(case.code, case.code, f'/cases/{case.case_id}')}"
                if case.summary:
                    line += f": {case.summary[:160]}"
            lines.append(line)
        for name in names:
            if name not in {po.name for po in orders}:
                lines.append(f"{name}: not found in Odoo")
        ctx.block("Orders named in the question", lines)

    async def _suppliers_block(self, ctx: Context, question: str) -> None:
        if self._performance is None:
            return
        words = {w.lower() for w in re.findall(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{4,}", question)}
        if not words:
            return
        try:
            rows = await self._performance.scores()
        except ScError as exc:
            logger.warning("scores unavailable for the assistant: {}", exc)
            return
        lines = []
        for row in rows:
            name = str(row.get("partner_name") or "")
            if not any(w in name.lower() for w in words):
                continue
            otif = row.get("otif")
            lines.append(
                f"{ctx.cite(name, name, '/suppliers', replace=True)} score {row.get('score')}/100"
                + (f", OTIF {round(100 * float(otif))}%" if otif is not None else "")
                + (
                    f", lead time {float(row['lead_time_mean_days']):.0f} days"
                    if row.get("lead_time_mean_days") is not None
                    else ""
                )
                + (f"; {row['scorecard']}" if row.get("scorecard") else "")
            )
        if lines:
            ctx.block("Suppliers named in the question (latest scorecard)", lines)

    async def _products_block(self, ctx: Context, question: str) -> None:
        codes = {c.upper() for c in CODE_LIKE.findall(question.upper())} - set(
            PO_NAME.findall(question.upper())
        )
        if not codes or self._planning is None:
            return
        runs = await self._planning.runs(limit=1)
        if not runs:
            return
        lines = []
        run_path = f"/planning/{runs[0].run_id}"
        for row in await self._planning.lines(runs[0].run_id):
            line = row.line
            if line.product_ref.upper() not in codes:
                continue
            ctx.product_ids[line.product_ref.upper()] = line.product_id
            lines.append(
                f"{ctx.cite(line.product_ref, line.product_name or line.product_ref, run_path)} "
                f"{line.product_name or ''}: position {line.position:g}, forecast "
                f"{line.forecast_daily:.2f}/day ({line.forecast_method}), reorder at {line.rop:g}, "
                f"order up to {line.order_up_to:g}, proposed {line.order_qty:g} "
                f"({line.action}) from {line.supplier_name or 'no reference supplier'}"
            )
        if lines:
            ctx.block(f"Products named in the question (plan {runs[0].as_of})", lines)

    # --- plans ----------------------------------------------------------------------------

    def _sanitize(self, plan: Plan | None, ctx: Context) -> Plan | None:
        """Keep only steps the desk can run: known products, orders and playbooks."""
        if plan is None:
            return None
        steps: list[PlanStep] = []
        for step in plan.steps:
            if step.kind == "quote_round":
                ref = (step.product_ref or "").upper()
                product_id = ctx.product_ids.get(ref)
                if product_id is None or not step.qty:
                    continue
                steps.append(step.model_copy(update={"product_ref": ref, "product_id": product_id}))
                continue
            po = (step.po_name or "").upper()
            if step.kind in ORDER_STEPS and not PO_NAME.fullmatch(po):
                continue
            if step.kind == "hold_until" and step.until is None:
                continue
            if step.kind == "start_playbook" and step.playbook not in ctx.playbooks:
                continue
            steps.append(step.model_copy(update={"po_name": po}))
        if not steps:
            return None
        return plan.model_copy(update={"steps": steps})


class PlanRunner:
    """Runs a confirmed plan step by step through the agents; says what each step did."""

    def __init__(
        self,
        *,
        cases: CaseStore,
        actions: ChatActions,
        sourcing: SourcingDispatcher | None = None,
        playbooks: PlaybookEngine | None = None,
    ) -> None:
        self._cases = cases
        self._actions = actions
        self._sourcing = sourcing
        self._playbooks = playbooks

    async def execute(self, plan: Plan, *, by: Any) -> list[str]:
        outcomes: list[str] = []
        for step in plan.steps:
            try:
                outcomes.append(await self._one(step, by))
            except ScError as exc:
                outcomes.append(f"{step.kind}: failed ({exc.message})")
            except (LookupError, ValueError) as exc:
                outcomes.append(f"{step.kind}: failed ({exc})")
        return outcomes

    async def _one(self, step: PlanStep, by: Any) -> str:
        if step.kind in ("quote_round", "alternate_source"):
            if self._sourcing is None:
                raise LookupError("the sourcing agent is not deployed")
            task = SourcingTask(
                kind=step.kind,
                case_id=new_id("plan"),
                po_name=step.po_name if step.kind == "alternate_source" else None,
                product_id=step.product_id,
                qty=step.qty,
                partner_ids=list(step.partner_ids),
                deadline_days=step.deadline_days,
                reason=f"assistant plan by {by.email}: {step.explanation}"[:500],
            )
            result = await self._sourcing.run(task, requested_by=by.email)
            return f"{step.kind} {step.product_ref or step.po_name}: {result.get('summary')}"
        if step.kind == "start_playbook":
            if self._playbooks is None:
                raise LookupError("playbooks are not available")
            assert step.po_name and step.playbook
            run = await self._playbooks.start(
                step.playbook, po_name=step.po_name, partner_id=None, started_by=by.email
            )
            return f"playbook {step.playbook} started on {step.po_name} (run #{run.id})"
        assert step.po_name is not None
        open_cases = await self._cases.open_for_po(step.po_name)
        if open_cases:
            case = open_cases[0]
        else:
            case, _ = await self._cases.attach_or_create(
                kind="eta", po_name=step.po_name, partner_id=None, agent="director"
            )
        action = ProposedAction(
            kind=step.kind,  # type: ignore[arg-type]
            note=step.note,
            until=step.until,
            explanation=step.explanation,
        )
        outcome = await self._actions.execute(case, action, by=by)
        return f"{step.kind} {step.po_name}: {outcome}"


def hold_until_datetime(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


__all__ = [
    "AssistantAnswer",
    "AssistantMessage",
    "AssistantStore",
    "Citation",
    "DepartmentAssistant",
    "MemoryAssistantStore",
    "Plan",
    "PlanRunner",
    "PlanStep",
    "PostgresAssistantStore",
]
