"""The inventory planning graph.

    load -> forecast -> compute -> detect -> review -> explain -> propose
        -> [planning_run approval] -> apply | rejected        (daily_plan, review_product)
        -> no_action                                          (nothing actionable)
        -> END                                                (what_if: no approval, no writes)

``review`` only acts on ``review_product`` tasks: the model reads the
product's notes and the reason for the review and may put a line on hold,
switch it to the alternate supplier (numbers recomputed by the formulas)
or send it to a person.

Numbers come from ``forecast`` and ``compute``; ``explain`` and ``propose``
are the only nodes that call the model, and only to write text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from inventory_planning.nodes.apply import (
    ACTIONABLE,
    PLAN_STEP,
    make_apply,
    make_plan_approval,
    make_rejected,
)
from inventory_planning.nodes.propose import (
    make_compute,
    make_detect,
    make_explain,
    make_forecast,
    make_load,
    make_propose,
)
from inventory_planning.nodes.review import make_review
from inventory_planning.policy import ParamsStore
from inventory_planning.ports import DataPorts, WritePorts
from inventory_planning.runs import RunStore
from inventory_planning.state import Node, PlanningState
from sc_core.graph import ApprovalGateway
from sc_core.infra.settings import LangfuseCfg, PlanningCfg
from sc_core.llm import ChatCompleter
from sc_core.schema.events import RfqDrafted
from sc_core.shared.time import local_today

Publish = Callable[[RfqDrafted], Awaitable[Any]]


async def _no_publish(_: RfqDrafted) -> None:
    return None


@dataclass
class Deps:
    data: DataPorts
    writes: WritePorts
    params: ParamsStore
    runs: RunStore
    chat: ChatCompleter
    approvals: ApprovalGateway
    cfg: PlanningCfg = field(default_factory=PlanningCfg)
    publish: Publish = _no_publish
    langfuse: LangfuseCfg | None = None
    today: Callable[[], date] = field(default=local_today)


def build_graph(deps: Deps, checkpointer: Any) -> CompiledStateGraph:
    g: StateGraph = StateGraph(PlanningState)
    _add(g, "load", make_load(deps.data, deps.cfg, today=deps.today))
    _add(g, "forecast", make_forecast())
    _add(g, "compute", make_compute(deps.params, deps.cfg))
    _add(g, "detect", make_detect())
    _add(g, "review", make_review(deps.chat, deps.cfg, langfuse=deps.langfuse))
    _add(g, "explain", make_explain(deps.chat, langfuse=deps.langfuse))
    _add(g, "no_action", _no_action)
    _add(g, "propose", make_propose(deps.chat, deps.runs, langfuse=deps.langfuse))
    _add(g, "apply", make_apply(deps.writes, deps.runs, publish=deps.publish, today=deps.today))
    _add(g, "rejected", make_rejected(deps.runs))

    g.add_edge(START, "load")
    g.add_conditional_edges("load", _continue_or_end, {"go": "forecast", "end": END})
    g.add_edge("forecast", "compute")
    g.add_edge("compute", "detect")
    g.add_edge("detect", "review")
    g.add_edge("review", "explain")
    g.add_edge("explain", "propose")
    g.add_conditional_edges(
        "propose",
        _after_propose,
        {"approval": f"{PLAN_STEP}.request", "nothing": "no_action", "end": END},
    )
    deps.approvals.add_approval(
        g,
        step=PLAN_STEP,
        build=make_plan_approval(),
        after=None,
        approved="apply",
        rejected="rejected",
    )
    g.add_edge("apply", END)
    g.add_edge("rejected", END)
    g.add_edge("no_action", END)
    return g.compile(checkpointer=checkpointer)


def _add(g: StateGraph, name: str, node: Node) -> None:
    # LangGraph's add_node overloads do not accept a Callable alias; the runtime contract holds.
    g.add_node(name, node)  # type: ignore[call-overload, arg-type]


def _continue_or_end(state: dict[str, Any]) -> Hashable:
    return "end" if state.get("outcome") else "go"


def _after_propose(state: dict[str, Any]) -> Hashable:
    if state.get("outcome"):
        return "end"
    actionable = any(ln.get("action") in ACTIONABLE for ln in state.get("lines") or [])
    return "approval" if actionable else "nothing"


async def _no_action(state: Any) -> dict[str, Any]:
    lines = state.get("lines") or []
    held = [ln["product_ref"] for ln in lines if ln.get("action") == "hold"]
    manual = [ln["product_ref"] for ln in lines if ln.get("action") == "manual_review"]
    parts = ["nada que aplicar"]
    if held:
        parts.append(f"en espera: {', '.join(held)}")
    if manual:
        parts.append(f"revisión manual: {', '.join(manual)}")
    explanation = next((ln.get("explanation") for ln in lines if ln.get("explanation")), None)
    if explanation:
        parts.append(str(explanation))
    return {"outcome": {"status": "no_action", "summary": "; ".join(parts)[:500]}}
