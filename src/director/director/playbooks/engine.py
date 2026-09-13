"""The playbook engine: start a run for an order, move it step by step.

``advance`` walks a run from its current step: a step whose condition does
not hold is skipped; a wait step parks the run with a due date (an
``until`` condition can end it early); an agent step sends the task through
the same path the director uses for everything else (task_sent event, the
agent, the outcome on the case) and, when the agent pauses on an approval,
the run waits for the decision; an action closes the case or escalates. An
agent that is not deployed (the sourcing agent before S4) is replaced by the
step's fallback, an escalation with the reason, so a plan never dies quietly.

Runs move on the hourly ``playbooks`` tick, when an event lands on the
order (a reply, a receipt, a resolved approval) and when the daily
follow-up job starts new ones.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from loguru import logger

from director.agents import Agents
from director.escalation import Escalator
from director.playbooks.conditions import holds
from director.playbooks.model import Playbook, Step, load_playbooks
from director.playbooks.store import ACTIVE, PlaybookRun, PlaybookStore, StepRecord
from director.policies import PoFacts
from director.store import Case, CaseKind, CaseStore
from director.workflow import (
    ConversationLookup,
    NoConversations,
    consolidate_outcome,
    outcome_from_reply,
)
from sc_core.schema.a2a import SupplierCommsTask
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError
from sc_core.shared.time import local_today


class FactsPort(Protocol):
    async def facts_for(self, po_name: str, today: date) -> PoFacts | None: ...


class PlaybookPosition(StrictModel):
    """Where a run is, in words the UI shows on cards, drawers and approvals."""

    run_id: int
    playbook: str
    title: str
    status: str
    step_id: str | None = None
    step_label: str | None = None
    step_index: int = 0
    steps_total: int = 0
    due_at: datetime | None = None
    next_steps: list[str] = []
    if_rejected: str | None = None


class PlaybookEngine:
    def __init__(
        self,
        *,
        store: PlaybookStore,
        cases: CaseStore,
        agents: Agents,
        escalator: Escalator,
        facts: FactsPort,
        conversations: ConversationLookup | None = None,
        playbooks: dict[str, Playbook] | None = None,
        today: Callable[[], date] = local_today,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._cases = cases
        self._agents = agents
        self._escalator = escalator
        self._facts = facts
        self._conversations = conversations or NoConversations()
        self._playbooks = playbooks if playbooks is not None else load_playbooks()
        self._today = today
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def playbooks(self) -> dict[str, Playbook]:
        return self._playbooks

    # --- starting --------------------------------------------------------------------

    async def start(
        self,
        name: str,
        *,
        po_name: str,
        partner_id: int | None,
        started_by: str | None = None,
        advance: bool = True,
    ) -> PlaybookRun:
        """Start ``name`` for an order; an order runs one playbook at a time."""
        playbook = self._playbooks[name]
        running = await self._store.active_for_po(po_name)
        if running:
            return running[0]
        case, _ = await self._cases.attach_or_create(
            kind=playbook.case_kind,  # type: ignore[arg-type]
            po_name=po_name,
            partner_id=partner_id,
            agent="director",
        )
        run = await self._store.create(
            playbook=name,
            case_id=case.case_id,
            po_name=po_name,
            partner_id=partner_id,
            started_by=started_by,
        )
        await self._cases.add_event(
            case.case_id,
            "playbook",
            {"playbook": name, "run_id": run.id, "event": "started", "by": started_by},
        )
        logger.bind(playbook=name, po_name=po_name, run_id=run.id).info("playbook started")
        if advance:
            run = await self.advance(run)
        return run

    # --- moving ----------------------------------------------------------------------

    async def tick(self) -> dict[str, Any]:
        """Every active run gets a chance to move: due waits, resolved approvals."""
        moved: list[int] = []
        for run in await self._store.active():
            before = (run.status, run.step_index)
            after = await self.advance(run)
            if (after.status, after.step_index) != before:
                moved.append(run.id)
        return {"active": len(await self._store.active()), "moved": moved}

    async def on_request(self, po_names: list[str]) -> list[int]:
        """An internal request became these RFQs: keep the requester informed (S6)."""
        if "internal_request" not in self._playbooks:
            return []
        started = []
        for po_name in po_names:
            run = await self.start(
                "internal_request", po_name=po_name, partner_id=None, started_by="director"
            )
            started.append(run.id)
        return started

    async def on_po_event(self, po_name: str) -> list[int]:
        """Something happened on the order (a reply, a receipt, a decision): try to move."""
        moved = []
        for run in await self._store.active_for_po(po_name):
            after = await self.advance(run)
            if (after.status, after.step_index) != (run.status, run.step_index):
                moved.append(run.id)
        return moved

    async def advance(self, run: PlaybookRun) -> PlaybookRun:
        playbook = self._playbooks.get(run.playbook)
        if playbook is None or not run.active:
            return run
        today = self._today()
        facts = await self._facts.facts_for(run.po_name, today) if run.po_name else None
        if facts is None:
            return await self._finish(run, "failed", "the order is gone or unreadable")
        case = await self._cases.get(run.case_id)
        if case is None:
            return await self._finish(run, "failed", "the case is gone")
        if run.status == "waiting_approval":
            if case.status in ("awaiting_approval",):
                return run  # still a person's turn
            run = await self._store.update(run.id, status="running", step_index=run.step_index + 1)
        if playbook.done_when and holds(playbook.done_when, facts, today):
            reason = playbook.done_reason or f"{playbook.title}: {playbook.done_when}"
            await self._cases.update(case.case_id, status="done", summary=reason)
            return await self._finish(run, "done", reason)
        while True:
            step = playbook.step(run.step_index)
            if step is None:
                return await self._finish(run, "done", "every step done")
            if not holds(step.when, facts, today):
                await self._record(run, step, "skipped", {"when": step.when})
                run = await self._store.update(run.id, step_index=run.step_index + 1)
                continue
            if step.kind == "wait":
                if run.status != "waiting":
                    due = self._now() + timedelta(days=step.wait_days or 0)
                    run = await self._store.update(
                        run.id, status="waiting", due_at=due, waiting_for=step.until
                    )
                    await self._record(run, step, "waiting", {"due_at": due.isoformat()})
                early = step.until is not None and holds(step.until, facts, today)
                if not (early or (run.due_at is not None and run.due_at <= self._now())):
                    # parked: the case is open with the plan's position as its summary
                    await self._cases.update(
                        case.case_id, status="open", summary=self._summary(playbook, step, run)
                    )
                    return run
                if True:
                    await self._record(run, step, "done", {"early": early})
                    run = await self._store.update(
                        run.id,
                        status="running",
                        step_index=run.step_index + 1,
                        due_at=None,
                        waiting_for=None,
                    )
                    continue
            if step.kind == "agent":
                outcome_status = await self._run_agent(run, step, case, facts, today)
                if outcome_status == "awaiting_approval":
                    return await self._store.update(run.id, status="waiting_approval")
                if outcome_status == "escalated":
                    return await self._finish(
                        run, "done", f"{step.describe()}: a person has it now"
                    )
                if outcome_status == "failed":
                    return await self._finish(run, "failed", f"{step.describe()}: failed")
                run = await self._store.update(
                    run.id, status="running", step_index=run.step_index + 1
                )
                continue
            # action
            if step.action == "close":
                await self._cases.update(
                    case.case_id, status="done", summary=step.reason or "playbook done"
                )
                await self._record(run, step, "done", {})
                return await self._finish(run, "done", step.reason or f"{playbook.title}: closed")
            reason = step.reason or f"{playbook.title}: {step.id}"
            await self._escalate(run, step, case, reason)
            return await self._finish(run, "done", reason)

    async def _run_agent(
        self, run: PlaybookRun, step: Step, case: Case, facts: PoFacts, today: date
    ) -> str:
        assert step.agent and step.task
        thread_id = f"pb_{run.id}_{step.id}"
        try:
            proxy = self._agents.for_name(step.agent)
        except LookupError:
            if step.fallback == "skip":
                await self._record(run, step, "skipped", {"reason": f"{step.agent} not deployed"})
                return "skipped"
            await self._escalate(
                run,
                step,
                case,
                f"{step.agent} is not deployed yet; {step.describe()} needs a person",
            )
            return "escalated"
        task_json = self._task_json(step, thread_id, facts, today)
        await self._cases.add_event(
            case.case_id,
            "task_sent",
            {
                "agent": step.agent,
                "task": step.task,
                "thread_id": thread_id,
                "po_name": facts.po_name,
                "playbook": run.playbook,
                "step": step.id,
            },
        )
        try:
            reply = await proxy.send(task_json, case_id=case.case_id)
        except (ScError, LookupError) as exc:
            error = exc if isinstance(exc, ScError) else ScError(str(exc))
            outcome = outcome_from_reply(case, step.agent, step.task, thread_id, None, error=error)
        else:
            outcome = outcome_from_reply(case, step.agent, step.task, thread_id, reply)
        update = await consolidate_outcome(
            self._cases, self._escalator, self._conversations, outcome
        )
        await self._record(
            run,
            step,
            "done" if outcome.status in ("sent", "applied", "no_action") else outcome.status,
            {
                "outcome": outcome.status,
                "approval_id": outcome.approval_id,
                "summary": outcome.summary[:200],
            },
        )
        return outcome.status if update.status != "escalated" else "escalated"

    @staticmethod
    def _task_json(step: Step, thread_id: str, facts: PoFacts, today: date) -> str:
        """The task as the agent expects it; other agents get the plain facts."""
        if step.agent == "supplier_comms":
            return SupplierCommsTask(
                kind=step.task,  # type: ignore[arg-type]
                case_id=thread_id,
                po_name=facts.po_name,
                days_silent=facts.silent_days(today),
                notes=step.notes,
            ).model_dump_json()
        return json.dumps(
            {
                "kind": step.task,
                "case_id": thread_id,
                "po_name": facts.po_name,
                "partner_id": facts.partner_id,
                "notes": step.notes,
            }
        )

    async def _escalate(self, run: PlaybookRun, step: Step, case: Case, reason: str) -> None:
        escalation = await self._escalator.escalate(
            case, reason=reason, details={"playbook": run.playbook, "step": step.id}
        )
        await self._cases.add_event(
            case.case_id,
            "escalated",
            {
                "reason": reason,
                "summary": escalation.summary,
                "approval_id": escalation.approval_id,
            },
        )
        await self._cases.update(case.case_id, status="escalated", summary=escalation.summary)
        await self._record(
            run, step, "escalated", {"approval_id": escalation.approval_id, "reason": reason}
        )

    async def _record(
        self, run: PlaybookRun, step: Step, status: str, detail: dict[str, Any]
    ) -> None:
        await self._store.record_step(
            run.id, StepRecord(step_id=step.id, status=status, at=self._now(), detail=detail)
        )

    @staticmethod
    def _summary(playbook: Playbook, step: Step, run: PlaybookRun) -> str:
        due = f" until {run.due_at:%d %b %H:%M}" if run.due_at else ""
        return f"{playbook.title}: {step.describe()}{due}"

    async def _finish(self, run: PlaybookRun, status: str, summary: str) -> PlaybookRun:
        finished = await self._store.update(
            run.id,
            status=status,
            finished_at=self._now(),
            summary=summary,
            due_at=None,
            waiting_for=None,
        )
        await self._cases.add_event(
            run.case_id,
            "playbook",
            {"playbook": run.playbook, "run_id": run.id, "event": status, "summary": summary},
        )
        case = await self._cases.get(run.case_id)
        if status == "done" and case is not None and case.status == "open":
            await self._cases.update(run.case_id, status="done", summary=summary)
        logger.bind(playbook=run.playbook, run_id=run.id, status=status).info("playbook {}", status)
        return finished

    async def cancel(self, run_id: int, *, by: str) -> PlaybookRun:
        run = await self._store.get(run_id)
        if run is None or not run.active:
            raise ScError(f"playbook run {run_id} is not active")
        return await self._finish(run, "cancelled", f"cancelled by {by}")

    # --- reading ---------------------------------------------------------------------

    def position(self, run: PlaybookRun) -> PlaybookPosition:
        playbook = self._playbooks.get(run.playbook)
        if playbook is None:
            return PlaybookPosition(
                run_id=run.id, playbook=run.playbook, title=run.playbook, status=run.status
            )
        step = playbook.step(run.step_index)
        remaining = playbook.steps[run.step_index + 1 :]
        if_rejected = None
        if step is not None and step.kind == "agent":
            # a rejected approval fails the step: the run stops and a person has it
            if_rejected = "the playbook stops and the order stays with a person"
        return PlaybookPosition(
            run_id=run.id,
            playbook=run.playbook,
            title=playbook.title,
            status=run.status,
            step_id=step.id if step else None,
            step_label=step.describe() if step else None,
            step_index=run.step_index,
            steps_total=len(playbook.steps),
            due_at=run.due_at,
            next_steps=[s.describe() for s in remaining],
            if_rejected=if_rejected,
        )

    async def position_for_po(self, po_name: str) -> PlaybookPosition | None:
        runs = await self._store.active_for_po(po_name)
        return self.position(runs[0]) if runs else None

    async def position_for_case(self, case_id: str) -> PlaybookPosition | None:
        run = await self._store.active_for_case(case_id)
        return self.position(run) if run else None

    async def counts(self) -> dict[str, dict[str, int]]:
        """Active runs per playbook and step id, for the Playbooks page."""
        out: dict[str, dict[str, int]] = {name: {} for name in self._playbooks}
        for run in await self._store.active():
            playbook = self._playbooks.get(run.playbook)
            if playbook is None:
                continue
            step = playbook.step(run.step_index)
            key = step.id if step else "end"
            out[run.playbook][key] = out[run.playbook].get(key, 0) + 1
        return out


__all__ = ["ACTIVE", "CaseKind", "FactsPort", "PlaybookEngine", "PlaybookPosition"]
