"""The orchestration workflow, expressed with the Microsoft Agent Framework.

One event, one run::

    Orchestrator.handle(event)
        route(event)                  pure table (router.py)
        case = attach or create       cases store
        with trace(session=case)      Langfuse session = case id
            workflow.run(WorkItem)    route → agent proxies → consolidate

Executors are thin: ``RouteExecutor`` fans the routed work out as typed
messages, one ``AgentProxyExecutor`` per agent posts tasks over A2A, and
``ConsolidateExecutor`` writes the outcome on the case and escalates when a
human is needed. Messages are routed by type, so an event that needs no
agent goes from route straight to consolidate.

A workflow instance cannot run twice at once, so ``build_workflow`` is
called per event. Building is cheap; the executors hold no state.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from agent_framework import Executor, Workflow, WorkflowBuilder, WorkflowContext, handler
from loguru import logger
from pydantic import ValidationError

from director.agents import Agents
from director.escalation import Escalator
from director.inbox import EventResults
from director.jobs import JobRunner
from director.router import Dispatch, Route, UnroutableEvent, route
from director.store import Case, CaseStatus, CaseStore
from sc_core.infra import tracing
from sc_core.schema.a2a import OutcomeStatus, SupplierCommsResult
from sc_core.schema.base import StrictModel
from sc_core.schema.events import BaseEvent, ScheduledTick
from sc_core.shared.errors import ScError

# --- messages between executors ---------------------------------------------------


class WorkItem(StrictModel):
    """What enters the workflow: the event, its route and the case it belongs to."""

    event: BaseEvent
    route: Route
    case: Case


class TaskEnvelope(StrictModel):
    """One agent task to post, with the case it reports to."""

    case: Case
    event_id: str
    dispatch: Dispatch


class AgentOutcome(StrictModel):
    """What came back from an agent (or the failure to reach it)."""

    case: Case
    agent: str
    task_kind: str
    thread_id: str
    status: OutcomeStatus
    summary: str
    run_id: str | None = None
    approval_id: int | None = None
    sent_message_id: str | None = None
    error: dict[str, Any] | None = None


class RecordOnly(StrictModel):
    """Nothing to send: a note, an escalation without agent, or a job to run."""

    case: Case
    event: BaseEvent
    route: Route


class CaseUpdate(StrictModel):
    """The workflow's output for one event."""

    case_id: str
    status: CaseStatus
    kind: Literal["agent", "note", "escalated", "job"]
    detail: dict[str, Any] = {}


# --- ports --------------------------------------------------------------------------


@runtime_checkable
class ConversationLookup(Protocol):
    async def conversation_for(self, graph_message_id: str) -> str | None:
        """The Outlook conversation of a message the agents sent (``mail_outbound``)."""
        ...


class NoConversations:
    async def conversation_for(self, graph_message_id: str) -> str | None:
        return None


@dataclass
class Deps:
    cases: CaseStore
    agents: Agents
    escalator: Escalator
    jobs: JobRunner
    conversations: ConversationLookup


# --- status mapping -----------------------------------------------------------------

_CASE_STATUS: dict[str, CaseStatus] = {
    "sent": "done",
    "applied": "done",
    "no_action": "done",
    "awaiting_approval": "awaiting_approval",
    "rejected": "rejected",
    "escalated": "escalated",
    "failed": "failed",
}

NEEDS_HUMAN: frozenset[str] = frozenset({"failed", "escalated"})


def case_status_for(outcome_status: str) -> CaseStatus:
    return _CASE_STATUS.get(outcome_status, "failed")


# --- executors ----------------------------------------------------------------------


class RouteExecutor(Executor):
    """Fan the routed work out: one envelope per task, or a record-only message."""

    @handler
    async def route(self, item: WorkItem, ctx: WorkflowContext[TaskEnvelope | RecordOnly]) -> None:
        if not item.route.dispatches:
            await ctx.send_message(RecordOnly(case=item.case, event=item.event, route=item.route))
            return
        for dispatch in item.route.dispatches:
            await ctx.send_message(
                TaskEnvelope(case=item.case, event_id=item.event.event_id, dispatch=dispatch)
            )


class AgentProxyExecutor(Executor):
    """Post one agent's tasks over A2A and turn the reply into an ``AgentOutcome``."""

    def __init__(self, agent_name: str, deps: Deps) -> None:
        super().__init__(id=agent_name)
        self._agent = agent_name
        self._deps = deps

    @handler
    async def run(self, envelope: TaskEnvelope, ctx: WorkflowContext[AgentOutcome]) -> None:
        if envelope.dispatch.agent != self._agent:
            return  # another proxy's task (fan-out delivers to every successor)
        task = envelope.dispatch.task
        case = envelope.case
        await self._deps.cases.add_event(
            case.case_id,
            "task_sent",
            {
                "agent": self._agent,
                "task": task.kind,
                "thread_id": task.case_id,
                "po_name": task.po_name,
                "event_id": envelope.event_id,
            },
        )
        proxy = self._deps.agents.for_name(self._agent)
        log = logger.bind(case_id=case.case_id, thread_id=task.case_id, kind=task.kind)
        try:
            reply = await proxy.send(task.model_dump_json(), case_id=case.case_id)
        except ScError as exc:
            log.opt(exception=True).error("agent call failed: {}", exc.message)
            outcome = outcome_from_reply(
                case, self._agent, task.kind, task.case_id, None, error=exc
            )
        else:
            outcome = outcome_from_reply(case, self._agent, task.kind, task.case_id, reply)
        await ctx.send_message(outcome)


def outcome_from_reply(
    case: Case,
    agent: str,
    task_kind: str,
    thread_id: str,
    reply: Any,
    *,
    error: ScError | None = None,
) -> AgentOutcome:
    """Parse the agent's JSON result; a transport error or other text is a failure."""
    if error is not None or reply is None:
        message = error.message if error is not None else "no reply"
        return AgentOutcome(
            case=case,
            agent=agent,
            task_kind=task_kind,
            thread_id=thread_id,
            status="failed",
            summary=f"{agent} unreachable: {message}"[:500],
            error=error.to_dict() if error is not None else None,
        )
    try:
        result = SupplierCommsResult.model_validate_json(reply.text)
    except ValidationError:
        text = (reply.text or f"agent replied with status {reply.status} and no result")[:500]
        return AgentOutcome(
            case=case,
            agent=agent,
            task_kind=task_kind,
            thread_id=thread_id,
            status="failed" if reply.status != "completed" else "no_action",
            summary=text,
        )
    outbound = result.outbound
    return AgentOutcome(
        case=case,
        agent=agent,
        task_kind=result.kind,
        thread_id=result.case_id,
        status=result.status,
        summary=result.outcome.summary,
        run_id=result.run_id,
        approval_id=result.outcome.approval_id,
        sent_message_id=outbound.sent_message_id if outbound else None,
    )


class ConsolidateExecutor(Executor):
    """Write outcomes on the case; escalate what needs a human; yield the update."""

    def __init__(self, deps: Deps) -> None:
        super().__init__(id="consolidate")
        self._deps = deps

    @handler
    async def agent_result(
        self, outcome: AgentOutcome, ctx: WorkflowContext[Any, CaseUpdate]
    ) -> None:
        deps = self._deps
        await ctx.yield_output(
            await consolidate_outcome(deps.cases, deps.escalator, deps.conversations, outcome)
        )

    @handler
    async def record(self, item: RecordOnly, ctx: WorkflowContext[Any, CaseUpdate]) -> None:
        cases, case, decided = self._deps.cases, item.case, item.route
        if decided.escalate:
            status = await self._escalate(case, reason=decided.escalate, details={})
            await ctx.yield_output(
                CaseUpdate(case_id=case.case_id, status=status, kind="escalated")
            )
            return
        if decided.job and isinstance(item.event, ScheduledTick):
            summary = await self._deps.jobs.run(decided.job, item.event)
            await cases.add_event(case.case_id, "note", {"job": decided.job, **summary})
            updated = await cases.update(case.case_id, status="done", summary=f"job {decided.job}")
            await ctx.yield_output(
                CaseUpdate(case_id=case.case_id, status=updated.status, kind="job", detail=summary)
            )
            return
        note = decided.note or f"{item.event.type} recorded"
        await cases.add_event(case.case_id, "note", {"text": note, "event_type": item.event.type})
        status = case.status
        if getattr(item.event, "status", None) == "expired":
            status = await self._escalate(case, reason=note, details={})
        elif case.status == "open" and not await _has_agent_work(cases, case.case_id):
            status = (await cases.update(case.case_id, status="done", summary=note)).status
        elif item.event.type == "agent.run_finished":
            finished = case_status_for(str(getattr(item.event, "status", "failed")))
            status = (await cases.update(case.case_id, status=finished, summary=note)).status
            if finished in ("failed", "escalated"):
                status = await self._escalate(case, reason=note, details={})
        await ctx.yield_output(CaseUpdate(case_id=case.case_id, status=status, kind="note"))

    async def _escalate(self, case: Case, *, reason: str, details: dict[str, Any]) -> CaseStatus:
        return await escalate_case(
            self._deps.cases, self._deps.escalator, case, reason=reason, details=details
        )


async def _has_agent_work(cases: CaseStore, case_id: str) -> bool:
    return any(e.kind == "task_sent" for e in await cases.events(case_id))


async def escalate_case(
    cases: CaseStore, escalator: Escalator, case: Case, *, reason: str, details: dict[str, Any]
) -> CaseStatus:
    """Hand the case to a person and record it; returns the new case status."""
    escalation = await escalator.escalate(case, reason=reason, details=details)
    await cases.add_event(
        case.case_id,
        "escalated",
        {
            "reason": reason,
            "summary": escalation.summary,
            "approval_id": escalation.approval_id,
            "trace_url": escalation.trace_url,
        },
    )
    updated = await cases.update(case.case_id, status="escalated", summary=escalation.summary)
    return updated.status


async def consolidate_outcome(
    cases: CaseStore,
    escalator: Escalator,
    conversations: ConversationLookup,
    outcome: AgentOutcome,
) -> CaseUpdate:
    """Write an agent outcome on its case; escalate when a human is needed."""
    case = outcome.case
    await cases.add_event(
        case.case_id,
        "result",
        {
            "agent": outcome.agent,
            "task": outcome.task_kind,
            "thread_id": outcome.thread_id,
            "run_id": outcome.run_id,
            "status": outcome.status,
            "summary": outcome.summary,
            "approval_id": outcome.approval_id,
            "error": outcome.error,
        },
    )
    conversation = None
    if outcome.sent_message_id:
        conversation = await conversations.conversation_for(outcome.sent_message_id)
    status = case_status_for(outcome.status)
    await cases.update(
        case.case_id,
        status=status,
        summary=outcome.summary,
        agent=outcome.agent,
        conversation_id=conversation,
    )
    if outcome.approval_id is not None:
        await cases.add_event(
            case.case_id,
            "approval_requested",
            {"approval_id": outcome.approval_id, "agent": outcome.agent},
        )
    if outcome.status in NEEDS_HUMAN:
        status = await escalate_case(
            cases,
            escalator,
            case,
            reason=outcome.summary,
            details={"agent": outcome.agent, "task": outcome.task_kind, "error": outcome.error},
        )
    return CaseUpdate(
        case_id=case.case_id,
        status=status,
        kind="agent",
        detail={
            "agent": outcome.agent,
            "task": outcome.task_kind,
            "status": outcome.status,
            "summary": outcome.summary,
            "approval_id": outcome.approval_id,
        },
    )


# --- building and running -----------------------------------------------------------

AGENT_NAMES = ("supplier_comms",)  # phase 7 adds inventory_planning, phase 9 logistics


def build_workflow(deps: Deps) -> Workflow:
    router = RouteExecutor(id="route")
    consolidate = ConsolidateExecutor(deps)
    builder = WorkflowBuilder(start_executor=router, name="director")
    builder.add_edge(router, consolidate)
    for name in AGENT_NAMES:
        proxy = AgentProxyExecutor(name, deps)
        builder.add_edge(router, proxy)
        builder.add_edge(proxy, consolidate)
    return builder.build()


@contextmanager
def _trace(event: BaseEvent, case: Case) -> Iterator[Any]:
    """The case is the Langfuse session; the producer's trace, when any, is continued."""
    if event.trace_id:
        with tracing.continue_trace(event.trace_id, "director.workflow", case_id=case.case_id) as s:
            yield s
    else:
        with tracing.start_case(case.case_id, "director.workflow") as s:
            yield s


class Orchestrator:
    """Entry point: one call per accepted event. Never raises; the inbox row gets the outcome."""

    def __init__(self, deps: Deps, results: EventResults) -> None:
        self._deps = deps
        self._results = results

    async def handle(self, event: BaseEvent) -> dict[str, Any]:
        log = logger.bind(event_id=event.event_id, event_type=event.type)
        try:
            decided = route(event)
        except UnroutableEvent as exc:
            unroutable: dict[str, Any] = {"status": "unroutable", "error": str(exc)}
            await self._results.record(event.event_id, unroutable)
            log.error("no route for event")
            return unroutable
        case = await self._case_for(event, decided)
        await self._deps.cases.add_event(
            case.case_id,
            "event_received",
            {"event_id": event.event_id, "event_type": event.type, "source": event.source},
        )
        if decided.snapshot:
            await self._deps.cases.add_event(case.case_id, "promise", decided.snapshot)
        result: dict[str, Any]
        with _trace(event, case):
            if trace_id := tracing.current_trace_id():
                await self._deps.cases.update(case.case_id, trace_id=trace_id)
            try:
                run = await build_workflow(self._deps).run(
                    WorkItem(event=event, route=decided, case=case)
                )
                updates = [u for u in run.get_outputs() if isinstance(u, CaseUpdate)]
                result = {
                    "case_id": case.case_id,
                    "updates": [u.model_dump(mode="json") for u in updates],
                }
            except Exception as exc:  # noqa: BLE001 - a background task must not die silently
                log.opt(exception=True).error("workflow failed: {}", exc)
                result = {"case_id": case.case_id, "status": "failed", "error": str(exc)[:500]}
                await self._deps.cases.update(case.case_id, status="failed", summary=str(exc)[:500])
        await self._results.record(event.event_id, result)
        log.bind(case_id=case.case_id).info("event handled")
        return result

    async def _case_for(self, event: BaseEvent, decided: Route) -> Case:
        thread_id = getattr(event, "thread_id", None)
        if thread_id:
            found = await self._deps.cases.find_by_thread(str(thread_id))
            if found is not None:
                return found
        case, _created = await self._deps.cases.attach_or_create(
            kind=decided.case_kind,
            po_name=decided.po_name,
            partner_id=decided.partner_id,
            conversation_id=decided.conversation_id,
        )
        return case
