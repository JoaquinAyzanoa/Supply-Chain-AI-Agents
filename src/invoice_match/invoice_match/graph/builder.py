"""The invoice matching graph.

load_context -> read_invoice -> match -> (no order / duplicate: end) ->
[vendor_bill approval] -> apply | rejected.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from invoice_match.graph.nodes.decide import (
    BILL_STEP,
    make_apply,
    make_bill_approval,
    make_rejected,
)
from invoice_match.graph.nodes.load import make_load_context
from invoice_match.graph.nodes.match import make_match
from invoice_match.graph.nodes.read import make_read_invoice
from invoice_match.graph.nodes.record import make_record_check
from invoice_match.graph.state import InvoiceMatchState, Node
from invoice_match.infra.ports import InvoicePorts
from sc_core.graph import ApprovalGateway
from sc_core.i18n import Language
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter
from sc_core.shared.time import local_today


@dataclass
class Deps:
    ports: InvoicePorts
    chat: ChatCompleter
    approvals: ApprovalGateway
    language: Language = "en"
    price_tolerance_pct: float = 1.0
    qty_tolerance_pct: float = 0.0
    fuzzy_threshold: float = 0.6
    runtime: RuntimeSettingsReader | None = None  # Control Tower tolerance wins over cfg
    max_attachment_chars: int = 12_000
    langfuse: LangfuseCfg | None = None
    today: Callable[[], date] = field(default=local_today)


def build_graph(deps: Deps, checkpointer: Any) -> CompiledStateGraph:
    g: StateGraph = StateGraph(InvoiceMatchState)
    _add(
        g,
        "load_context",
        make_load_context(deps.ports, max_attachment_chars=deps.max_attachment_chars),
    )
    _add(g, "read_invoice", make_read_invoice(deps.chat, langfuse=deps.langfuse, today=deps.today))
    _add(
        g,
        "match",
        make_match(
            deps.ports,
            price_tolerance_pct=deps.price_tolerance_pct,
            runtime=deps.runtime,
            qty_tolerance_pct=deps.qty_tolerance_pct,
            fuzzy_threshold=deps.fuzzy_threshold,
            language=deps.language,
        ),
    )
    _add(g, "record_check", make_record_check(deps.ports, language=deps.language))
    _add(g, "apply", make_apply(deps.ports, language=deps.language))
    _add(g, "rejected", make_rejected(deps.ports, language=deps.language))

    g.add_edge(START, "load_context")
    g.add_conditional_edges("load_context", _continue_or_end, {"go": "read_invoice", "end": END})
    g.add_edge("read_invoice", "match")
    g.add_conditional_edges("match", _continue_or_end, {"go": "record_check", "end": END})
    g.add_edge("record_check", f"{BILL_STEP}.request")
    deps.approvals.add_approval(
        g,
        step=BILL_STEP,
        build=make_bill_approval(language=deps.language),
        after=None,
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
