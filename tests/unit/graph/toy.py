"""A two-step toy agent: draft -> approval -> send, used by unit and integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sc_core.graph import (
    ApprovalGateway,
    ApprovalRequest,
    BaseAgentState,
    clear_sensitive,
)


class ToyState(BaseAgentState, total=False):
    inbound_text: str | None
    draft: str | None
    sent: bool
    outcome: str | None
    auto: bool


@dataclass
class FakeApprovalPorts:
    created: list[dict[str, Any]] = field(default_factory=list)
    reviews: list[dict[str, Any]] = field(default_factory=list)

    async def create_approval(self, **kwargs: Any) -> int:
        self.created.append(kwargs)
        return 100 + len(self.created)

    async def schedule_review(self, **kwargs: Any) -> None:
        self.reviews.append(kwargs)


def build_toy(ports: FakeApprovalPorts, checkpointer: Any) -> CompiledStateGraph:
    gateway = ApprovalGateway(
        ports,
        agent_name="toy",
        callback_url="http://toy/approvals/callback",
        callback_secret="s",
        approver_user_id=2,
        deadline_days=2,
    )

    async def draft(state: ToyState) -> dict[str, Any]:
        return {"draft": f"Hola, re: {state.get('inbound_text', '')}"}

    async def build(state: dict[str, Any]) -> ApprovalRequest:
        return ApprovalRequest(
            kind="send_email",
            summary="send reply",
            payload={"body": state["draft"]},
            po_id=7,
            auto_approve=bool(state.get("auto")),
            auto_reason="trusted supplier" if state.get("auto") else None,
        )

    async def send(state: ToyState) -> dict[str, Any]:
        return {"sent": True, "outcome": "sent", **clear_sensitive()}

    async def rejected(state: ToyState) -> dict[str, Any]:
        return {"sent": False, "outcome": "rejected", **clear_sensitive()}

    g: StateGraph = StateGraph(ToyState)
    g.add_node("draft", draft)
    g.add_node("send", send)
    g.add_node("rejected", rejected)
    g.add_edge(START, "draft")
    gateway.add_approval(
        g, step="send", build=build, after="draft", approved="send", rejected="rejected"
    )
    g.add_edge("send", END)
    g.add_edge("rejected", END)
    return g.compile(checkpointer=checkpointer)
