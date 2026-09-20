"""The supplier communications graph.

Outbound: load_context -> draft_outbound -> create_draft -> [send_email
approval] -> send | rejected. Inbound: load_context -> classify ->
extract -> propose_changes -> [po_change approval] -> apply_changes |
change_rejected; a question is answered from records (``answer``) and sent
through the outbound path; a dispute goes to a person; anything else ends as
no_action. Phase 11 S6 adds internal requests (extract_request -> approval ->
create_request), price-list attachments (approval -> apply_price_list) and the
status replies a playbook sends to an internal requester.
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
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter
from sc_core.shared.time import local_today
from supplier_comms.nodes.answer import make_answer
from supplier_comms.nodes.apply import (
    CHANGE_STEP,
    make_apply_changes,
    make_change_approval,
    make_change_rejected,
    make_no_action,
)
from supplier_comms.nodes.classify import make_classify
from supplier_comms.nodes.common import fail
from supplier_comms.nodes.dispute import make_dispute
from supplier_comms.nodes.draft_outbound import KIND_TO_DRAFT, make_draft_outbound
from supplier_comms.nodes.extract import make_extract
from supplier_comms.nodes.internal_request import (
    REQUEST_STEP,
    make_create_request,
    make_extract_request,
    make_request_approval,
    make_request_rejected,
    make_status_reply,
)
from supplier_comms.nodes.load_context import make_load_context
from supplier_comms.nodes.partner import (
    PARTNER_STEP,
    make_create_partner,
    make_partner_approval,
    make_partner_rejected,
)
from supplier_comms.nodes.price_list import (
    PRICE_STEP,
    make_apply_price_list,
    make_price_list_approval,
    make_price_list_rejected,
)
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

TERMINAL = (
    "send",
    "rejected",
    "apply_changes",
    "change_rejected",
    "no_action",
    "unsupported",
    "create_partner",
    "partner_rejected",
    "dispute",
    "create_request",
    "request_rejected",
    "status_reply",
    "apply_price_list",
    "price_list_rejected",
)


@dataclass
class Deps:
    ports: AgentPorts
    chat: ChatCompleter
    approvals: ApprovalGateway
    toolbox: ToolBox | None = None
    runtime: RuntimeSettingsReader | None = None  # Control Tower settings
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
            deps.ports,
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
    _add(g, "create_partner", make_create_partner(deps.ports, language=deps.language))
    _add(g, "partner_rejected", make_partner_rejected(language=deps.language))
    # phase 11 S6: answers from records, disputes, internal requests, price lists
    _add(
        g,
        "answer",
        make_answer(
            deps.chat,
            deps.toolbox,
            deps.ports,
            max_tool_rounds=deps.max_tool_rounds,
            langfuse=deps.langfuse,
            today=deps.today,
            language=deps.language,
        ),
    )
    _add(g, "dispute", make_dispute(deps.approvals, language=deps.language))
    _add(
        g,
        "extract_request",
        make_extract_request(
            deps.ports,
            deps.chat,
            deps.approvals,
            langfuse=deps.langfuse,
            today=deps.today,
            language=deps.language,
        ),
    )
    _add(
        g,
        "create_request",
        make_create_request(deps.ports, sleep=deps.sleep, language=deps.language),
    )
    _add(
        g,
        "request_rejected",
        make_request_rejected(deps.ports, sleep=deps.sleep, language=deps.language),
    )
    _add(g, "status_reply", make_status_reply(deps.ports, sleep=deps.sleep, language=deps.language))
    _add(g, "apply_price_list", make_apply_price_list(deps.ports, language=deps.language))
    _add(g, "price_list_rejected", make_price_list_rejected(language=deps.language))

    g.add_edge(START, "load_context")
    g.add_conditional_edges(
        "load_context",
        _after_load,
        {
            "outbound": "draft_outbound",
            "inbound": "classify",
            "resolve": "resolve_unlinked",
            "price_list": f"{PRICE_STEP}.request",
            "request": "extract_request",
            "status": "status_reply",
            "unsupported": "unsupported",
            "end": END,
        },
    )
    deps.approvals.add_approval(
        g,
        step=PRICE_STEP,
        build=make_price_list_approval(language=deps.language),
        after=None,
        approved="apply_price_list",
        rejected="price_list_rejected",
    )
    g.add_conditional_edges(
        "extract_request", _continue_or_end, {"go": f"{REQUEST_STEP}.request", "end": END}
    )
    deps.approvals.add_approval(
        g,
        step=REQUEST_STEP,
        build=make_request_approval(language=deps.language),
        after=None,
        approved="create_request",
        rejected="request_rejected",
    )
    # A resolved message goes back through load_context with the chosen order; an
    # unknown sender's quotation pauses on the partner_create approval.
    g.add_conditional_edges(
        "resolve_unlinked",
        _after_resolve,
        {"go": "load_context", "partner": f"{PARTNER_STEP}.request", "end": END},
    )
    deps.approvals.add_approval(
        g,
        step=PARTNER_STEP,
        build=make_partner_approval(language=deps.language),
        after=None,
        approved="create_partner",
        rejected="partner_rejected",
    )
    g.add_conditional_edges("draft_outbound", _continue_or_end, {"go": "create_draft", "end": END})
    deps.approvals.add_approval(
        g,
        step=SEND_STEP,
        build=make_send_approval(language=deps.language),
        after="create_draft",
        approved="send",
        rejected="rejected",
    )
    g.add_conditional_edges(
        "classify",
        _after_classify,
        {
            "extract": "extract",
            "answer": "answer",
            "dispute": "dispute",
            "no_action": "no_action",
        },
    )
    g.add_conditional_edges("answer", _continue_or_end, {"go": "create_draft", "end": END})
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
        return "price_list" if state.get("price_list") else "inbound"
    if kind == "resolve_unlinked":
        if state.get("chosen_po_name"):
            return "inbound"
        return "price_list" if state.get("price_list") else "resolve"
    if kind == "internal_request":
        return "request"
    if kind == "status_reply":
        return "status"
    return "unsupported"


def _after_classify(state: dict[str, Any]) -> Hashable:
    kind = (state.get("classification") or {}).get("kind")
    if kind in ("quotation", "eta_update"):
        return "extract"
    if kind == "question":
        return "answer"
    if kind == "dispute":
        return "dispute"
    return "no_action"


def _after_resolve(state: dict[str, Any]) -> Hashable:
    if state.get("outcome"):
        return "end"
    return "partner" if state.get("partner_candidate") else "go"


def _continue_or_end(state: dict[str, Any]) -> Hashable:
    return "end" if state.get("outcome") else "go"


async def _unsupported(state: Any) -> dict[str, Any]:
    kind = (state.get("task") or {}).get("kind")
    return fail(f"task kind {kind!r} is not implemented yet")
