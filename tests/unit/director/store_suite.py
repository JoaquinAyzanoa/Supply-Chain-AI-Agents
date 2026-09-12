"""Behaviour every ``CaseStore`` must satisfy; run on memory (unit) and Postgres (integration)."""

from __future__ import annotations

from datetime import UTC, datetime

from director.store import CaseStore


async def check_attach_same_po_and_thread(store: CaseStore) -> None:
    first, created = await store.attach_or_create(
        kind="rfq", po_name="P00015", partner_id=7, conversation_id="conv-1"
    )
    assert created and first.status == "open" and first.is_open
    again, created = await store.attach_or_create(
        kind="inbound", po_name="P00015", conversation_id="conv-1"
    )
    assert not created and again.case_id == first.case_id
    assert again.kind == "rfq"  # the case keeps its original kind


async def check_new_case_for_other_thread_or_po(store: CaseStore) -> None:
    first, _ = await store.attach_or_create(kind="rfq", po_name="P00015", conversation_id="conv-1")
    other_thread, created = await store.attach_or_create(
        kind="inbound", po_name="P00015", conversation_id="conv-2"
    )
    assert created and other_thread.case_id != first.case_id
    other_po, created = await store.attach_or_create(
        kind="inbound", po_name="P00016", conversation_id="conv-1"
    )
    assert created and other_po.case_id not in (first.case_id, other_thread.case_id)
    assert len(await store.open_for_po("P00015")) == 2


async def check_unknown_thread_attaches_and_learns(store: CaseStore) -> None:
    """A PO confirmed by Odoo has no conversation yet; the first reply teaches it."""
    eta, _ = await store.attach_or_create(kind="eta", po_name="P00015", partner_id=None)
    reply, created = await store.attach_or_create(
        kind="inbound", po_name="P00015", partner_id=7, conversation_id="conv-9"
    )
    assert not created and reply.case_id == eta.case_id
    assert reply.conversation_id == "conv-9" and reply.partner_id == 7
    scheduler, created = await store.attach_or_create(kind="eta", po_name="P00015")
    assert not created and scheduler.case_id == eta.case_id
    unrelated, created = await store.attach_or_create(
        kind="inbound", po_name="P00015", conversation_id="conv-10"
    )
    assert created and unrelated.case_id != eta.case_id


async def check_terminal_cases_never_attach(store: CaseStore) -> None:
    done, _ = await store.attach_or_create(kind="rfq", po_name="P00015", conversation_id="c")
    await store.update(done.case_id, status="done", summary="RFQ sent and answered")
    fresh, created = await store.attach_or_create(
        kind="inbound", po_name="P00015", conversation_id="c"
    )
    assert created and fresh.case_id != done.case_id
    paused, _ = await store.attach_or_create(kind="eta", po_name="P00016")
    await store.update(paused.case_id, status="awaiting_approval")
    same, created = await store.attach_or_create(kind="inbound", po_name="P00016")
    assert not created and same.case_id == paused.case_id
    escalated, _ = await store.attach_or_create(kind="eta", po_name="P00017")
    await store.update(escalated.case_id, status="escalated")
    same, created = await store.attach_or_create(kind="inbound", po_name="P00017")
    assert not created, "an escalated case still collects the story until a human closes it"


async def check_no_po_always_new(store: CaseStore) -> None:
    a, created_a = await store.attach_or_create(kind="unlinked", po_name=None)
    b, created_b = await store.attach_or_create(kind="unlinked", po_name=None)
    assert created_a and created_b and a.case_id != b.case_id


async def check_update_events_and_list(store: CaseStore) -> None:
    case, _ = await store.attach_or_create(kind="rfq", po_name="P00015", agent="supplier_comms")
    when = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    updated = await store.update(
        case.case_id, status="awaiting_approval", trace_id="tr1", next_action_at=when
    )
    assert updated.status == "awaiting_approval" and updated.trace_id == "tr1"
    assert updated.next_action_at == when and updated.updated_at >= case.updated_at
    cleared = await store.update(case.case_id, next_action_at=None)
    assert cleared.next_action_at is None and cleared.status == "awaiting_approval"
    untouched = await store.update(case.case_id)
    assert untouched.trace_id == "tr1"

    first = await store.add_event(
        case.case_id, "task_sent", {"agent": "supplier_comms", "task": "send_rfq", "n": 1}
    )
    second = await store.add_event(case.case_id, "result", {"status": "awaiting_approval"})
    assert second > first
    events = await store.events(case.case_id)
    assert [e.kind for e in events] == ["task_sent", "result"]
    assert events[0].payload == {"agent": "supplier_comms", "task": "send_rfq", "n": 1}
    assert await store.events("nope") == []

    other, _ = await store.attach_or_create(kind="eta", po_name="P00016")
    assert [c.case_id for c in await store.list(status="awaiting_approval")] == [case.case_id]
    assert [c.case_id for c in await store.list(po_name="P00016")] == [other.case_id]
    assert len(await store.list()) == 2 and len(await store.list(limit=1)) == 1
    assert await store.get(case.case_id) is not None and await store.get("nope") is None


async def check_unknown_case_errors(store: CaseStore) -> None:
    for action in (
        store.update("nope", status="done"),
        store.add_event("nope", "note", {"text": "x"}),
    ):
        try:
            await action
        except KeyError:
            continue
        raise AssertionError("expected KeyError for an unknown case")


CHECKS = [
    check_attach_same_po_and_thread,
    check_new_case_for_other_thread_or_po,
    check_unknown_thread_attaches_and_learns,
    check_terminal_cases_never_attach,
    check_no_po_always_new,
    check_update_events_and_list,
    check_unknown_case_errors,
]
