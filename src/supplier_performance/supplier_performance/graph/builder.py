"""The weekly run.

load_history -> compute -> scorecards -> [supplier_score approval] -> apply | rejected.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sc_core.graph import ApprovalGateway
from sc_core.i18n import Language
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter
from sc_core.shared.time import local_today
from supplier_performance.domain.metrics import Weights
from supplier_performance.graph.nodes.steps import (
    SCORE_STEP,
    make_apply,
    make_compute,
    make_load_history,
    make_rejected,
    make_score_approval,
    make_scorecards,
)
from supplier_performance.graph.state import Node, PerformanceState
from supplier_performance.infra.ports import PerformancePorts


@dataclass
class Deps:
    ports: PerformancePorts
    chat: ChatCompleter
    approvals: ApprovalGateway
    weights: Weights = field(default_factory=Weights)
    months: int = 12
    max_suppliers: int = 50
    language: Language = "en"
    langfuse: LangfuseCfg | None = None
    today: Callable[[], date] = field(default=local_today)


def build_graph(deps: Deps, checkpointer: Any) -> CompiledStateGraph:
    g: StateGraph = StateGraph(PerformanceState)
    _add(
        g,
        "load_history",
        make_load_history(
            deps.ports, months=deps.months, max_suppliers=deps.max_suppliers, today=deps.today
        ),
    )
    _add(g, "compute", make_compute(deps.ports, weights=deps.weights))
    _add(
        g,
        "scorecards",
        make_scorecards(deps.chat, deps.ports, langfuse=deps.langfuse, language=deps.language),
    )
    _add(g, "apply", make_apply(deps.ports, language=deps.language))
    _add(g, "rejected", make_rejected(language=deps.language))
    g.add_edge(START, "load_history")
    g.add_conditional_edges("load_history", _continue_or_end, {"go": "compute", "end": END})
    g.add_edge("compute", "scorecards")
    deps.approvals.add_approval(
        g,
        step=SCORE_STEP,
        build=make_score_approval(language=deps.language),
        after="scorecards",
        approved="apply",
        rejected="rejected",
    )
    g.add_edge("apply", END)
    g.add_edge("rejected", END)
    return g.compile(checkpointer=checkpointer)


def _add(g: StateGraph, name: str, node: Node) -> None:
    g.add_node(name, node)  # type: ignore[call-overload, arg-type]


def _continue_or_end(state: dict[str, Any]) -> Hashable:
    return "end" if state.get("outcome") else "go"
