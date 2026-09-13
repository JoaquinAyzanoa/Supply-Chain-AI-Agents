"""The logistics graph.

Shipping notice: load_context -> extract_shipment -> propose_eta ->
[po_change approval] -> apply_eta | eta_rejected. Receipt: load_context ->
reconcile -> (match: end) | draft_discrepancy -> create_draft ->
[send_email approval] -> send | rejected. ``report_discrepancy`` is the
receipt path with the person's words and no "match" exit.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from logistics.carriers import CarrierTracker, NoCarrierTracking
from logistics.nodes.common import fail
from logistics.nodes.eta import ETA_STEP, make_apply_eta, make_eta_approval, make_eta_rejected
from logistics.nodes.load import make_load_context
from logistics.nodes.mail import (
    SEND_STEP,
    make_create_draft,
    make_rejected,
    make_send,
    make_send_approval,
)
from logistics.nodes.receipt import make_draft_discrepancy, make_reconcile
from logistics.nodes.shipment import make_extract_shipment, make_propose_eta
from logistics.ports import LogisticsPorts
from logistics.state import LogisticsState, Node
from sc_core.graph import ApprovalGateway
from sc_core.i18n import Language
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter
from sc_core.shared.time import local_today

TERMINAL = ("send", "rejected", "apply_eta", "eta_rejected", "unsupported")


@dataclass
class Deps:
    ports: LogisticsPorts
    chat: ChatCompleter
    approvals: ApprovalGateway
    tracker: CarrierTracker = field(default_factory=NoCarrierTracking)
    language: Language = "en"
    tolerance_pct: float = 0.0
    max_attachment_chars: int = 12_000
    langfuse: LangfuseCfg | None = None
    today: Callable[[], date] = field(default=local_today)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)


def build_graph(deps: Deps, checkpointer: Any) -> CompiledStateGraph:
    g: StateGraph = StateGraph(LogisticsState)
    _add(
        g,
        "load_context",
        make_load_context(deps.ports, max_attachment_chars=deps.max_attachment_chars),
    )
    _add(
        g,
        "extract_shipment",
        make_extract_shipment(deps.chat, deps.tracker, langfuse=deps.langfuse, today=deps.today),
    )
    _add(g, "propose_eta", make_propose_eta(language=deps.language))
    _add(g, "apply_eta", make_apply_eta(deps.ports, language=deps.language))
    _add(g, "eta_rejected", make_eta_rejected(deps.ports, language=deps.language))
    _add(
        g,
        "reconcile",
        make_reconcile(deps.ports, tolerance_pct=deps.tolerance_pct, language=deps.language),
    )
    _add(
        g,
        "draft_discrepancy",
        make_draft_discrepancy(
            deps.chat, langfuse=deps.langfuse, today=deps.today, language=deps.language
        ),
    )
    _add(g, "create_draft", make_create_draft(deps.ports))
    _add(g, "send", make_send(deps.ports, sleep=deps.sleep, language=deps.language))
    _add(g, "rejected", make_rejected(deps.ports, language=deps.language))
    _add(g, "unsupported", _unsupported)

    g.add_edge(START, "load_context")
    g.add_conditional_edges(
        "load_context",
        _after_load,
        {
            "shipment": "extract_shipment",
            "receipt": "reconcile",
            "unsupported": "unsupported",
            "end": END,
        },
    )
    g.add_edge("extract_shipment", "propose_eta")
    g.add_conditional_edges(
        "propose_eta", _continue_or_end, {"go": f"{ETA_STEP}.request", "end": END}
    )
    deps.approvals.add_approval(
        g,
        step=ETA_STEP,
        build=make_eta_approval(language=deps.language),
        after=None,
        approved="apply_eta",
        rejected="eta_rejected",
    )
    g.add_conditional_edges("reconcile", _continue_or_end, {"go": "draft_discrepancy", "end": END})
    g.add_conditional_edges(
        "draft_discrepancy", _continue_or_end, {"go": "create_draft", "end": END}
    )
    deps.approvals.add_approval(
        g,
        step=SEND_STEP,
        build=make_send_approval(language=deps.language),
        after="create_draft",
        approved="send",
        rejected="rejected",
    )
    for terminal in TERMINAL:
        g.add_edge(terminal, END)
    return g.compile(checkpointer=checkpointer)


def _add(g: StateGraph, name: str, node: Node) -> None:
    g.add_node(name, node)  # type: ignore[call-overload, arg-type]


def _after_load(state: dict[str, Any]) -> Hashable:
    if state.get("outcome"):
        return "end"
    kind = (state.get("task") or {}).get("kind")
    if kind == "track_shipment":
        return "shipment"
    if kind in ("reconcile_receipt", "report_discrepancy"):
        return "receipt"
    return "unsupported"


def _continue_or_end(state: dict[str, Any]) -> Hashable:
    return "end" if state.get("outcome") else "go"


async def _unsupported(state: Any) -> dict[str, Any]:
    kind = (state.get("task") or {}).get("kind")
    return fail(f"task kind {kind!r} is not implemented")
