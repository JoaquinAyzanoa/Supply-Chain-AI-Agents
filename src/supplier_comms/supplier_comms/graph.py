"""The supplier communications graph.

Outbound: load_context -> draft_outbound -> create_draft -> [send_email
approval] -> send | rejected. Inbound: load_context -> classify ->
extract -> propose_changes -> [po_change approval] -> apply_changes |
change_rejected; a question is answered in the thread through the outbound
path; anything else ends as no_action. ``resolve_unlinked`` arrives in story 6.
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
from sc_core.i18n import Language
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter
from sc_core.shared.time import local_today
from supplier_comms.nodes.apply import (
    CHANGE_STEP,
    make_apply_changes,
    make_change_approval,
    make_change_rejected,
    make_no_action,
)
from supplier_comms.nodes.classify import make_classify
from supplier_comms.nodes.common import fail
from supplier_comms.nodes.draft_outbound import KIND_TO_DRAFT, make_draft_outbound
from supplier_comms.nodes.extract import make_extract
from supplier_comms.nodes.load_context import make_load_context
from supplier_comms.nodes.propose import make_propose_changes
from supplier_comms.nodes.resolve import make_resolve_unlinked
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

TERMINAL = ("send", "rejected", "apply_changes", "change_rejected", "no_action", "unsupported")


@dataclass
class Deps:
    ports: AgentPorts
    chat: ChatCompleter
    approvals: ApprovalGateway
    toolbox: ToolBox | None = None
    auto_send_partner_ids: frozenset[int] = frozenset()
    language: Language = "en"  # for what people read; emails follow the supplier's language
    max_tool_rounds: int = 6
    max_attachment_chars: int = 12_000
    langfuse: LangfuseCfg | None = None
    today: Callable[[], date] = field(default=local_today)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)

    def __post_init__(self) -> None:
        if self.toolbox is None:
            self.toolbox = build_toolbox(self.ports)


def build_graph(deps: Deps, checkpointer: Any) -> CompiledStateGraph:
    assert deps.toolbox is not None
    g: StateGraph = StateGraph(SupplierCommsState)
    _add(
        g,
        "load_context",
        make_load_context(deps.ports, max_attachment_chars=deps.max_attachment_chars),
    )
    _add(
        g,
        "draft_outbound",
        make_draft_outbound(
            deps.chat,
            deps.toolbox,
            max_tool_rounds=deps.max_tool_rounds,
            langfuse=deps.langfuse,
            today=deps.today,
            language=deps.language,
        ),
    )
    _add(g, "create_draft", make_create_draft(deps.ports))
    _add(g, "send", make_send(deps.ports, sleep=deps.sleep, language=deps.language))
    _add(g, "rejected", make_rejected(deps.ports, language=deps.language))
    _add(g, "classify", make_classify(deps.chat, langfuse=deps.langfuse, today=deps.today))
    _add(g, "extract", make_extract(deps.chat, langfuse=deps.langfuse, today=deps.today))
    _add(g, "propose_changes", make_propose_changes(language=deps.language))
    _add(g, "apply_changes", make_apply_changes(deps.ports, language=deps.language))
    _add(g, "change_rejected", make_change_rejected(deps.ports, language=deps.language))
    _add(g, "no_action", make_no_action(language=deps.language))
    _add(
        g,
        "resolve_unlinked",
        make_resolve_unlinked(
            deps.ports, deps.chat, deps.approvals, langfuse=deps.langfuse, today=deps.today
        ),
    )
    _add(g, "unsupported", _unsupported)

    g.add_edge(START, "load_context")
    g.add_conditional_edges(
        "load_context",
        _after_load,
        {
            "outbound": "draft_outbound",
            "inbound": "classify",
            "resolve": "resolve_unlinked",
            "unsupported": "unsupported",
            "end": END,
        },
    )
    # A resolved message goes back through load_context with the chosen order.
    g.add_conditional_edges(
        "resolve_unlinked", _continue_or_end, {"go": "load_context", "end": END}
    )
    g.add_conditional_edges("draft_outbound", _continue_or_end, {"go": "create_draft", "end": END})
    deps.approvals.add_approval(
        g,
        step=SEND_STEP,
        build=make_send_approval(deps.auto_send_partner_ids, language=deps.language),
        after="create_draft",
        approved="send",
        rejected="rejected",
    )
    g.add_conditional_edges(
        "classify",
        _after_classify,
        {"extract": "extract", "reply": "draft_outbound", "no_action": "no_action"},
    )
    g.add_edge("extract", "propose_changes")
    # propose_changes ends the run itself when there is nothing to change; the
    # approval nodes are only reached with a proposal in the state.
    g.add_conditional_edges(
        "propose_changes", _continue_or_end, {"go": f"{CHANGE_STEP}.request", "end": END}
    )
    deps.approvals.add_approval(
        g,
        step=CHANGE_STEP,
        build=make_change_approval(language=deps.language),
        after=None,
        approved="apply_changes",
        rejected="change_rejected",
    )
    for terminal in TERMINAL:
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)


def _add(g: StateGraph, name: str, node: Node) -> None:
    # LangGraph's add_node overloads do not accept a Callable alias; the runtime contract holds.
    g.add_node(name, node)  # type: ignore[call-overload, arg-type]


def _after_load(state: dict[str, Any]) -> Hashable:
    if state.get("outcome"):
        return "end"
    kind = (state.get("task") or {}).get("kind")
    if kind in KIND_TO_DRAFT:
        return "outbound"
    if kind == "handle_inbound":
        return "inbound"
    if kind == "resolve_unlinked":
        return "inbound" if state.get("chosen_po_name") else "resolve"
    return "unsupported"


def _after_classify(state: dict[str, Any]) -> Hashable:
    kind = (state.get("classification") or {}).get("kind")
    if kind in ("quotation", "eta_update"):
        return "extract"
    if kind == "question":
        return "reply"
    return "no_action"


def _continue_or_end(state: dict[str, Any]) -> Hashable:
    return "end" if state.get("outcome") else "go"


async def _unsupported(state: Any) -> dict[str, Any]:
    kind = (state.get("task") or {}).get("kind")
    return fail(f"task kind {kind!r} is not implemented yet")
