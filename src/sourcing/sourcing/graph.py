"""The sourcing graph.

quote_round:      load -> invite
compare_quotes:   load -> collect -> recommend -> [award approval] -> award_apply | award_rejected
counter_offer:    load -> plan_offer -> [negotiation_offer approval] -> send_offer | offer_rejected
alternate_source: load -> find_alternates -> (recommend -> award ...) | invite | escalated
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sc_core.graph import ApprovalGateway
from sc_core.i18n import Language
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter
from sourcing.compare import Weights
from sourcing.nodes.alternate import make_find_alternates
from sourcing.nodes.common import Limits, LimitsReader
from sourcing.nodes.compare import (
    AWARD_STEP,
    make_award_apply,
    make_award_approval,
    make_award_rejected,
    make_collect,
    make_recommend,
)
from sourcing.nodes.load import make_load
from sourcing.nodes.negotiate import (
    OFFER_STEP,
    make_offer_approval,
    make_offer_rejected,
    make_plan_offer,
    make_send_offer,
)
from sourcing.nodes.round import make_invite
from sourcing.ports import SourcingPorts
from sourcing.state import Node, SourcingState


async def _default_limits() -> Limits:
    return Limits()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Deps:
    ports: SourcingPorts
    chat: ChatCompleter
    approvals: ApprovalGateway
    limits: LimitsReader = field(default=_default_limits)
    weights: Weights = field(default_factory=Weights)
    language: Language = "en"
    langfuse: LangfuseCfg | None = None
    now: Callable[[], datetime] = field(default=_utc_now)


def build_graph(deps: Deps, checkpointer: Any) -> CompiledStateGraph:
    g: StateGraph = StateGraph(SourcingState)
    _add(g, "load", make_load(deps.ports))
    _add(g, "invite", make_invite(deps.ports, deps.limits, now=deps.now, language=deps.language))
    _add(
        g,
        "collect",
        make_collect(
            deps.ports, deps.limits, weights=deps.weights, now=deps.now, language=deps.language
        ),
    )
    _add(g, "recommend", make_recommend(deps.chat, langfuse=deps.langfuse, language=deps.language))
    _add(g, "award_apply", make_award_apply(deps.ports, now=deps.now, language=deps.language))
    _add(g, "award_rejected", make_award_rejected(deps.ports, language=deps.language))
    _add(
        g,
        "plan_offer",
        make_plan_offer(
            deps.ports,
            deps.limits,
            deps.chat,
            deps.approvals,
            langfuse=deps.langfuse,
            language=deps.language,
        ),
    )
    _add(g, "send_offer", make_send_offer(deps.ports, language=deps.language))
    _add(g, "offer_rejected", make_offer_rejected(deps.ports, language=deps.language))
    _add(
        g,
        "find_alternates",
        make_find_alternates(
            deps.ports,
            deps.limits,
            deps.approvals,
            weights=deps.weights,
            now=deps.now,
            language=deps.language,
        ),
    )

    g.add_edge(START, "load")
    g.add_conditional_edges(
        "load",
        _after_load,
        {
            "invite": "invite",
            "collect": "collect",
            "plan_offer": "plan_offer",
            "find_alternates": "find_alternates",
            "end": END,
        },
    )
    g.add_edge("invite", END)
    g.add_conditional_edges("collect", _continue_or_end, {"go": "recommend", "end": END})
    g.add_edge("recommend", f"{AWARD_STEP}.request")
    deps.approvals.add_approval(
        g,
        step=AWARD_STEP,
        build=make_award_approval(language=deps.language),
        after=None,
        approved="award_apply",
        rejected="award_rejected",
    )
    g.add_conditional_edges(
        "plan_offer", _continue_or_end, {"go": f"{OFFER_STEP}.request", "end": END}
    )
    deps.approvals.add_approval(
        g,
        step=OFFER_STEP,
        build=make_offer_approval(language=deps.language),
        after=None,
        approved="send_offer",
        rejected="offer_rejected",
    )
    g.add_conditional_edges(
        "find_alternates",
        _after_alternates,
        {"award": "recommend", "invite": "invite", "end": END},
    )
    for terminal in ("award_apply", "award_rejected", "send_offer", "offer_rejected"):
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)


def _add(g: StateGraph, name: str, node: Node) -> None:
    g.add_node(name, node)  # type: ignore[call-overload, arg-type]


def _after_load(state: dict[str, Any]) -> Hashable:
    if state.get("outcome"):
        return "end"
    kind = str((state.get("task") or {}).get("kind"))
    return {
        "quote_round": "invite",
        "compare_quotes": "collect",
        "counter_offer": "plan_offer",
        "alternate_source": "find_alternates",
    }.get(kind, "end")


def _continue_or_end(state: dict[str, Any]) -> Hashable:
    return "end" if state.get("outcome") else "go"


def _after_alternates(state: dict[str, Any]) -> Hashable:
    if state.get("outcome"):
        return "end"
    return "invite" if state.get("mode") == "invite" else "award"


__all__ = ["Deps", "build_graph"]
