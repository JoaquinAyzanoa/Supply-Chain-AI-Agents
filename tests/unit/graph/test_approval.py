"""Approval interrupt: created once, paused, resumed approved or rejected, auto-approved."""

from __future__ import annotations

from langgraph.types import Command

from sc_core.graph import cleared, decision_for, memory_checkpointer, run_config
from sc_core.graph.approval import ApprovalRequest, render_note

from .toy import FakeApprovalPorts, build_toy


async def test_interrupts_after_creating_the_approval_once() -> None:
    ports = FakeApprovalPorts()
    graph = build_toy(ports, memory_checkpointer())
    cfg = run_config("case_1")

    out = await graph.ainvoke({"case_id": "case_1", "run_id": "run_1", "inbound_text": "q"}, cfg)
    assert "__interrupt__" in out and out.get("sent") is None
    assert len(ports.created) == 1 and ports.created[0]["case_id"] == "case_1"
    assert ports.created[0]["callback_url"] == "http://toy/approvals/callback"
    assert ports.created[0]["payload"] == {"body": "Hola, re: q", "step": "send"}
    assert ports.reviews[0]["res_id"] == 7 and ports.reviews[0]["user_id"] == 2
    snapshot = await graph.aget_state(cfg)
    assert snapshot.next == ("send.await",)
    assert snapshot.values["pending_approvals"] == [
        {"approval_id": 101, "step": "send", "kind": "send_email"}
    ]
    assert out["__interrupt__"][0].value["approval_id"] == 101


async def test_resume_approved_continues_without_a_second_approval() -> None:
    ports = FakeApprovalPorts()
    graph = build_toy(ports, memory_checkpointer())
    cfg = run_config("case_2")
    await graph.ainvoke({"case_id": "case_2", "inbound_text": "q"}, cfg)

    final = await graph.ainvoke(
        Command(resume={"approval_id": 101, "status": "approved", "resolved_by": "ana"}), cfg
    )
    assert final["sent"] is True and final["outcome"] == "sent"
    assert len(ports.created) == 1  # the request node did not run again
    decision = decision_for(final, "send")
    assert decision is not None and decision.approved and decision.resolved_by == "ana"
    assert cleared(final)  # inbound_text blanked by the terminal node
    assert (await graph.aget_state(cfg)).next == ()


async def test_resume_rejected_takes_the_other_branch() -> None:
    ports = FakeApprovalPorts()
    graph = build_toy(ports, memory_checkpointer())
    cfg = run_config("case_3")
    await graph.ainvoke({"case_id": "case_3", "inbound_text": "q"}, cfg)
    final = await graph.ainvoke(
        Command(resume={"approval_id": 101, "status": "rejected", "reason": "too early"}), cfg
    )
    assert final["outcome"] == "rejected" and final["sent"] is False
    assert decision_for(final, "send").reason == "too early"  # type: ignore[union-attr]


async def test_auto_approval_never_pauses() -> None:
    ports = FakeApprovalPorts()
    graph = build_toy(ports, memory_checkpointer())
    final = await graph.ainvoke(
        {"case_id": "case_4", "inbound_text": "q", "auto": True}, run_config("case_4")
    )
    assert "__interrupt__" not in final and final["outcome"] == "sent"
    assert ports.created == []
    decision = decision_for(final, "send")
    assert decision is not None and decision.resolved_by == "auto"
    assert decision.reason == "trusted supplier"


def test_render_note_escapes_and_truncates() -> None:
    req = ApprovalRequest(
        kind="orderpoint_change", summary="a <b>", payload={"x": "<script>", "long": "y" * 400}
    )
    html = render_note(req)
    assert "&lt;script&gt;" in html and "a &lt;b&gt;" in html
    assert "y" * 299 + "…" in html and "y" * 300 not in html
