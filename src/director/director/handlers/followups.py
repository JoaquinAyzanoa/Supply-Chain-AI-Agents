"""The daily follow-up job: chase silent RFQs and late orders, escalate the rest.

For every open order we know about (late ones from Odoo, those due soon,
and every RFQ we emailed) the job assembles ``PoFacts``, asks the policy
what to do, records the rule that fired on the order's case, and either
sends a ``follow_up`` / ``request_eta`` task to supplier_comms or hands the
order to a person. Each decision is a ``rule_fired`` case event, so the
Control Tower can explain why an email was proposed.

It also reviews pending approvals: after ``approval_stale_days`` the
approver gets a reminder on the record, after ``approval_expire_days`` the
approval is expired (Odoo then calls the agent back and mirrors the
decision, which escalates the case).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from director.agents import Agents
from director.escalation import Escalator
from director.policies import Decision, FollowUpPolicy, PoFacts, decide
from director.store import Case, CaseKind, CaseStore
from director.workflow import (
    ConversationLookup,
    NoConversations,
    consolidate_outcome,
    outcome_from_reply,
)
from sc_core.i18n import Language, t
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.odoo.models import Approval, PurchaseOrder
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import Conflict, NotFound, ScError
from sc_core.shared.idempotency import new_id
from sc_core.shared.time import local_today, utc_now


@runtime_checkable
class OrdersPort(Protocol):
    async def late_open_orders(self, as_of: date) -> list[PurchaseOrder]: ...

    async def open_due_within(self, as_of: date, days: int) -> list[PurchaseOrder]: ...

    async def by_names(self, names: Sequence[str]) -> list[PurchaseOrder]: ...


@runtime_checkable
class MailActivity(Protocol):
    async def contacts(self) -> dict[str, tuple[date | None, date | None]]:
        """Per order name: (last email we sent, last supplier message linked)."""
        ...


@runtime_checkable
class ApprovalsPort(Protocol):
    async def pending(self) -> list[Approval]: ...

    async def remind(self, approval: Approval, *, days_pending: int) -> None:
        """Nudge the approver (chatter note on the record)."""
        ...

    async def expire(self, approval_id: int, *, reason: str) -> None: ...


class NoApprovals:
    async def pending(self) -> list[Approval]:
        return []

    async def remind(self, approval: Approval, *, days_pending: int) -> None:
        return None

    async def expire(self, approval_id: int, *, reason: str) -> None:
        return None


class FollowUpJob:
    """``JobRunner`` for ``po_followups`` (other job ids are recorded as not implemented)."""

    def __init__(
        self,
        *,
        policy: FollowUpPolicy,
        orders: OrdersPort,
        mail: MailActivity,
        cases: CaseStore,
        agents: Agents,
        escalator: Escalator,
        approvals: ApprovalsPort | None = None,
        conversations: ConversationLookup | None = None,
        today: Callable[[], date] = local_today,
        language: Language = "en",
        runtime: RuntimeSettingsReader | None = None,
    ) -> None:
        self._language = language
        self._base_policy = policy
        self._policy = policy
        self._runtime = runtime
        self._orders = orders
        self._mail = mail
        self._cases = cases
        self._agents = agents
        self._escalator = escalator
        self._approvals = approvals or NoApprovals()
        self._conversations = conversations or NoConversations()
        self._today = today

    async def effective_policy(self) -> FollowUpPolicy:
        """The environment's policy, replaced by the Control Tower's values when saved."""
        if self._runtime is None:
            return self._base_policy
        return self._base_policy.with_runtime(await self._runtime.current())

    async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]:
        if job_id != "po_followups":
            return {"job": job_id, "status": "not_implemented"}
        self._policy = await self.effective_policy()
        today = self._today()
        facts = await self.gather(today)
        decisions = [d for d in (decide(f, self._policy, today) for f in facts) if d]
        sent = escalated = 0
        outcomes: list[dict[str, Any]] = []
        skipped: list[str] = []
        for decision in decisions:
            # Every action is a model run and an email or an escalation: cap them per run.
            # What is skipped is not recorded on the case, so it fires on the next run.
            if sent + escalated >= self._policy.max_actions_per_run:
                skipped.append(decision.po_name)
                continue
            fact = next(f for f in facts if f.po_name == decision.po_name)
            outcome = await self.act(decision, fact, tick, today)
            outcomes.append(outcome)
            sent += outcome.get("task") is not None
            escalated += bool(outcome.get("escalated"))
        if skipped:
            logger.bind(run_id=tick.run_id, skipped=len(skipped)).warning(
                "follow-up cap reached; remaining orders wait for the next run"
            )
        reviewed = await self.review_approvals(today)
        logger.bind(run_id=tick.run_id, orders=len(facts), decisions=len(decisions)).info(
            "follow-up job done"
        )
        return {
            "job": job_id,
            "status": "ok",
            "orders": len(facts),
            "tasks_sent": sent,
            "escalated": escalated,
            "decisions": outcomes,
            "skipped": skipped,
            "approvals": reviewed,
        }

    async def review_approvals(self, today: date) -> dict[str, list[int]]:
        """Remind after ``approval_stale_days``, expire after ``approval_expire_days``."""
        reminded: list[int] = []
        expired: list[int] = []
        for approval in await self._approvals.pending():
            if approval.create_date is None:
                continue
            days = (today - _as_date(approval.create_date)).days
            case = await self._cases.find_by_thread(approval.thread_id or "")
            # An escalation is already a person's to answer: remind, never expire.
            if days >= self._policy.approval_expire_days and approval.kind != "escalation":
                reason = t("approval.expired", self._language, days=days)
                await self._approvals.expire(approval.id, reason=reason)
                expired.append(approval.id)
                if case is not None:
                    await self._cases.add_event(
                        case.case_id,
                        "note",
                        {
                            "text": f"approval {approval.id} expired: {reason}",
                            "approval_id": approval.id,
                        },
                    )
                continue
            if days >= self._policy.approval_stale_days and not await self._reminded(
                case, approval.id
            ):
                await self._approvals.remind(approval, days_pending=days)
                reminded.append(approval.id)
                if case is not None:
                    await self._cases.add_event(
                        case.case_id,
                        "note",
                        {
                            "text": f"approver reminded about approval {approval.id} ({days} days)",
                            "approval_id": approval.id,
                            "reminder": True,
                        },
                    )
        return {"reminded": reminded, "expired": expired}

    async def _reminded(self, case: Case | None, approval_id: int) -> bool:
        if case is None:
            return False  # nothing to dedupe on: remind at most once per run anyway
        return any(
            e.kind == "note"
            and e.payload.get("reminder")
            and e.payload.get("approval_id") == approval_id
            for e in await self._cases.events(case.case_id)
        )

    async def gather(self, today: date) -> list[PoFacts]:  # noqa: D102 - see module docstring
        """Facts for every order worth looking at, each order once."""
        contacts = await self._mail.contacts()
        orders: dict[str, PurchaseOrder] = {}
        for po in await self._orders.late_open_orders(today):
            orders[po.name] = po
        for po in await self._orders.open_due_within(
            today, self._policy.po_eta_request_before_days
        ):
            orders.setdefault(po.name, po)
        missing = [name for name in contacts if name not in orders]
        for po in await self._orders.by_names(missing) if missing else []:
            orders.setdefault(po.name, po)
        facts: list[PoFacts] = []
        for name, po in sorted(orders.items()):
            last_out, last_in = contacts.get(name, (None, None))
            open_cases = await self._cases.open_for_po(name)
            facts.append(
                PoFacts(
                    po_id=po.id,
                    po_name=name,
                    partner_id=po.partner_id.id,
                    state=po.state,
                    date_planned=po.date_planned.date() if po.date_planned else None,
                    receipt_status=po.receipt_status,
                    last_outbound_at=last_out,
                    last_inbound_at=last_in,
                    rules_fired=await self._cases.rules_fired(name),
                    # a person has it, or put it on hold until a later date (chat: hold_until)
                    awaiting_human=any(
                        c.status in ("awaiting_approval", "escalated")
                        or (c.next_action_at is not None and c.next_action_at.date() > today)
                        for c in open_cases
                    ),
                )
            )
        return facts

    async def act_now(
        self, po_name: str, *, requested_by: str, today: date | None = None
    ) -> dict[str, Any]:
        """ "Act now" from the exceptions board: the policy's step today, or a chase anyway."""
        today = today or self._today()
        self._policy = await self.effective_policy()
        fact = next((f for f in await self.gather(today) if f.po_name == po_name), None)
        if fact is None:
            raise NotFound(f"{po_name} is not an open order the follow-up job knows")
        if fact.awaiting_human:
            raise Conflict(f"{po_name} already waits for a person")
        if not (fact.is_rfq or fact.is_confirmed_open):
            raise Conflict(f"{po_name} has nothing to chase")
        decision = decide(fact, self._policy, today) or Decision(
            po_name=po_name,
            rule="manual",
            key=f"manual:{today.isoformat()}",
            task="follow_up" if fact.is_rfq else "request_eta",
            days=fact.silent_days(today) or 0,
            reason=f"requested from the Control Tower by {requested_by}",
        )
        run_id = new_id("run")
        tick = ScheduledTick(
            source="director",
            case_id=run_id,
            job_id="po_followups",
            run_id=run_id,
            scheduled_at=utc_now(),
            trigger="manual",
        )
        return await self.act(decision, fact, tick, today)

    async def act(
        self, decision: Decision, fact: PoFacts, tick: ScheduledTick, today: date
    ) -> dict[str, Any]:
        kind: CaseKind = "rfq" if fact.is_rfq else "eta"
        case, _ = await self._cases.attach_or_create(
            kind=kind, po_name=fact.po_name, partner_id=fact.partner_id, agent="supplier_comms"
        )
        await self._cases.add_event(
            case.case_id,
            "rule_fired",
            {
                "rule": decision.rule,
                "key": decision.key,
                "days": decision.days,
                "task": decision.task,
                "escalate": decision.escalate,
                "reason": decision.reason,
                "run_id": tick.run_id,
            },
        )
        record: dict[str, Any] = {
            "po_name": fact.po_name,
            "case_id": case.case_id,
            "rule": decision.rule,
            "task": decision.task,
        }
        if decision.escalate:
            escalation = await self._escalator.escalate(
                case, reason=decision.reason, details={"rule": decision.rule, "days": decision.days}
            )
            await self._cases.add_event(
                case.case_id,
                "escalated",
                {
                    "reason": decision.reason,
                    "summary": escalation.summary,
                    "approval_id": escalation.approval_id,
                },
            )
            await self._cases.update(case.case_id, status="escalated", summary=escalation.summary)
            record["escalated"] = True
            return record
        assert decision.task is not None
        record.update(await self._send(case, decision, fact, today))
        return record

    async def _send(
        self, case: Case, decision: Decision, fact: PoFacts, today: date
    ) -> dict[str, Any]:
        # One thread per order and day: a re-run the same day resumes nothing and
        # sends nothing twice (the rule already fired), a new day is a new thread.
        thread_id = f"followup_{fact.po_name.lower()}_{today.isoformat()}"
        task = SupplierCommsTask(
            kind=decision.task or "follow_up",
            case_id=thread_id,
            po_name=fact.po_name,
            days_silent=decision.days if decision.task == "follow_up" else None,
            notes=decision.reason,
        )
        await self._cases.add_event(
            case.case_id,
            "task_sent",
            {
                "agent": "supplier_comms",
                "task": task.kind,
                "thread_id": thread_id,
                "po_name": fact.po_name,
            },
        )
        try:
            reply = await self._agents.for_name("supplier_comms").send(
                task.model_dump_json(), case_id=case.case_id
            )
        except ScError as exc:
            outcome = outcome_from_reply(
                case, "supplier_comms", task.kind, thread_id, None, error=exc
            )
        else:
            outcome = outcome_from_reply(case, "supplier_comms", task.kind, thread_id, reply)
        update = await consolidate_outcome(
            self._cases, self._escalator, self._conversations, outcome
        )
        return {"status": update.status, "summary": outcome.summary}


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value
