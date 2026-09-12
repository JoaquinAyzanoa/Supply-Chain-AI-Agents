"""The inventory planning graph.

    load -> forecast -> compute -> detect -> explain -> propose
        -> [planning_run approval] -> apply | rejected        (daily_plan, review_product)
        -> END                                                (what_if: no approval, no writes)

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
    _add(g, "explain", make_explain(deps.chat, langfuse=deps.langfuse))
    _add(g, "propose", make_propose(deps.chat, deps.runs, langfuse=deps.langfuse))
    _add(g, "apply", make_apply(deps.writes, deps.runs, publish=deps.publish, today=deps.today))
    _add(g, "rejected", make_rejected(deps.runs))

    g.add_edge(START, "load")
    g.add_conditional_edges("load", _continue_or_end, {"go": "forecast", "end": END})
    g.add_edge("forecast", "compute")
    g.add_edge("compute", "detect")
    g.add_edge("detect", "explain")
    g.add_edge("explain", "propose")
    g.add_conditional_edges("propose", _continue_or_end, {"go": f"{PLAN_STEP}.request", "end": END})
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
    return g.compile(checkpointer=checkpointer)


def _add(g: StateGraph, name: str, node: Node) -> None:
    # LangGraph's add_node overloads do not accept a Callable alias; the runtime contract holds.
    g.add_node(name, node)  # type: ignore[call-overload, arg-type]


def _continue_or_end(state: dict[str, Any]) -> Hashable:
    return "end" if state.get("outcome") else "go"
