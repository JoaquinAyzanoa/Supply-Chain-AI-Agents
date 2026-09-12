"""The supplier communications graph.

Phase 5 story 4 wires the outbound path: load_context -> draft_outbound ->
create_draft -> [send_email approval] -> send | rejected. Inbound kinds are
routed to ``unsupported`` until story 5 adds classify/extract/propose/apply.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sc_core.graph import ApprovalGateway, ToolBox
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter
from sc_core.shared.time import local_today
from supplier_comms.nodes.common import fail
from supplier_comms.nodes.draft_outbound import KIND_TO_DRAFT, make_draft_outbound
from supplier_comms.nodes.load_context import make_load_context
from supplier_comms.nodes.send import (
    SEND_STEP,
    make_create_draft,
    make_rejected,
    make_send,
    make_send_approval,
)
from supplier_comms.ports import AgentPorts
from supplier_comms.state import Node, SupplierCommsState
from supplier_comms.tools import build_toolbox


@dataclass
class Deps:
    ports: AgentPorts
    chat: ChatCompleter
    approvals: ApprovalGateway
    toolbox: ToolBox | None = None
    auto_send_partner_ids: frozenset[int] = frozenset()
    max_tool_rounds: int = 6
    langfuse: LangfuseCfg | None = None
    today: Callable[[], date] = field(default=local_today)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)

    def __post_init__(self) -> None:
        if self.toolbox is None:
            self.toolbox = build_toolbox(self.ports)


def build_graph(deps: Deps, checkpointer: Any) -> CompiledStateGraph:
    assert deps.toolbox is not None
    g: StateGraph = StateGraph(SupplierCommsState)
    _add(g, "load_context", make_load_context(deps.ports))
    _add(
        g,
        "draft_outbound",
        make_draft_outbound(
            deps.chat,
            deps.toolbox,
            max_tool_rounds=deps.max_tool_rounds,
            langfuse=deps.langfuse,
            today=deps.today,
        ),
    )
    _add(g, "create_draft", make_create_draft(deps.ports))
    _add(g, "send", make_send(deps.ports, sleep=deps.sleep))
    _add(g, "rejected", make_rejected(deps.ports))
    _add(g, "unsupported", _unsupported)

    g.add_edge(START, "load_context")
    g.add_conditional_edges(
        "load_context",
        _after_load,
        {"outbound": "draft_outbound", "unsupported": "unsupported", "end": END},
    )
    g.add_conditional_edges("draft_outbound", _continue_or_end, {"go": "create_draft", "end": END})
    deps.approvals.add_approval(
        g,
        step=SEND_STEP,
        build=make_send_approval(deps.auto_send_partner_ids),
        after="create_draft",
        approved="send",
        rejected="rejected",
    )
    g.add_edge("send", END)
    g.add_edge("rejected", END)
    g.add_edge("unsupported", END)
    return g.compile(checkpointer=checkpointer)


def _add(g: StateGraph, name: str, node: Node) -> None:
    # LangGraph's add_node overloads do not accept a Callable alias; the runtime contract holds.
    g.add_node(name, node)  # type: ignore[call-overload]


def _after_load(state: dict[str, Any]) -> Hashable:
    if state.get("outcome"):
        return "end"
    kind = (state.get("task") or {}).get("kind")
    return "outbound" if kind in KIND_TO_DRAFT else "unsupported"


def _continue_or_end(state: dict[str, Any]) -> Hashable:
    return "end" if state.get("outcome") else "go"


async def _unsupported(state: Any) -> dict[str, Any]:
    kind = (state.get("task") or {}).get("kind")
    return fail(f"task kind {kind!r} is not implemented yet")
