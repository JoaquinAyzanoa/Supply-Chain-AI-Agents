"""SyncRunner with FakeGraph, in-memory state, fake ports and a scripted director."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from mail_sync.sync import SyncRunner, case_id_for
from sc_core.a2a.events import EventPublisher, MemoryOutbox
from sc_core.infra.locks import MemoryLock
from sc_core.infra.settings import EventsCfg, MailSyncCfg
from sc_core.mail.errors import DeltaExpired, GraphError
from sc_core.mail.testing import FakeGraph
from sc_core.schema.events import parse_event

from .fakes import FakePorts

SUPPLIER = "ventas.hidraulica.sc@gmail.com"


class Director:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.down = False

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if self.down:
                raise httpx.ConnectError("director down")
            self.events.append(parse_event(request.content))
            return httpx.Response(202, json={"accepted": True})

        return httpx.MockTransport(handler)


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def world() -> dict[str, Any]:
    graph = FakeGraph()
    ports = FakePorts()
    ports.add_po("P00015", partner_id=42)
    ports.contacts[SUPPLIER] = 42
    director = Director()
    from mail_sync.state import MemorySyncState

    state = MemorySyncState()
    lock = MemoryLock()
    outbox = MemoryOutbox()
    publisher = EventPublisher(
        EventsCfg(
            signing_secret=SecretStr("s"),
            director_url="http://director.test",
            max_attempts=2,
            backoff_seconds=0,
        ),
        outbox=outbox,
        http=httpx.AsyncClient(transport=director.transport()),
        sleep=_no_sleep,
    )
    runner = SyncRunner(
        graph=graph,
        state=state,
        ports=ports,
        publisher=publisher,
        lock=lock,
        cfg=MailSyncCfg(),
        mailbox="me",
    )
    return {
        "graph": graph,
        "ports": ports,
        "director": director,
        "state": state,
        "lock": lock,
        "outbox": outbox,
        "runner": runner,
    }


async def test_new_message_linked_once_and_event_published(world: dict[str, Any]) -> None:
    graph: FakeGraph = world["graph"]
    msg = graph.receive(subject="Re: [P00015] RFQ", sender=SUPPLIER, headers={"x-sc-po": "P00015"})
    report = await world["runner"].run()
    assert report.status == "ok" and report.fetched == 1 and report.linked == 1
    assert report.linked_po_names == ["P00015"]

    link = world["ports"].links[0]
    assert link.message_id == msg.id and link.confidence == "exact"
    assert link.case_id == case_id_for(msg.id)
    events = world["director"].events
    assert len(events) == 1 and events[0].type == "inbound_mail.linked"
    assert events[0].rule == "header" and events[0].case_id == case_id_for(msg.id)
    assert world["state"].processed[msg.id]["outcome"] == "linked"
    assert world["state"].delta_links["me"] == "fake-delta:1"

    # nothing new: the delta cursor moved on
    again = await world["runner"].run()
    assert again.fetched == 0 and len(world["director"].events) == 1


async def test_unlinked_message_emits_hint(world: dict[str, Any]) -> None:
    world["ports"].add_po("P00017", partner_id=42)
    world["graph"].receive(subject="consulta general", sender=SUPPLIER)
    report = await world["runner"].run()
    assert report.unlinked == 1 and report.linked == 0
    event = world["director"].events[0]
    assert event.type == "inbound_mail.unlinked"
    assert event.partner_id == 42 and event.open_po_names == ["P00015", "P00017"]
    assert event.sender_address == SUPPLIER
    assert world["ports"].links == []


async def test_already_processed_is_skipped_without_event(world: dict[str, Any]) -> None:
    msg = world["graph"].receive(subject="[P00015] x", sender=SUPPLIER)
    await world["state"].mark_processed(msg.id, outcome="linked")
    report = await world["runner"].run()
    assert report.skipped == 1 and report.linked == 0
    assert world["director"].events == []


async def test_delta_expired_triggers_full_resync_without_duplicates(
    world: dict[str, Any],
) -> None:
    graph: FakeGraph = world["graph"]
    graph.receive(subject="[P00015] a", sender=SUPPLIER)
    await world["runner"].run()
    graph.receive(subject="[P00015] b", sender=SUPPLIER)
    graph.fail_next(DeltaExpired())
    report = await world["runner"].run()
    assert report.full_resync is True and report.fetched == 2
    assert report.skipped == 1 and report.linked == 1
    assert len(world["director"].events) == 2


async def test_director_down_parks_event_then_next_run_delivers(world: dict[str, Any]) -> None:
    director: Director = world["director"]
    director.down = True
    world["graph"].receive(subject="[P00015] a", sender=SUPPLIER)
    report = await world["runner"].run()
    assert report.linked == 1 and director.events == []
    assert len(world["outbox"].rows) == 1
    assert world["state"].processed  # handled: the link exists, only delivery is pending

    director.down = False
    report = await world["runner"].run()
    assert report.outbox_delivered == 1 and world["outbox"].rows == {}
    assert len(director.events) == 1 and director.events[0].type == "inbound_mail.linked"


async def test_lock_held_means_skipped(world: dict[str, Any]) -> None:
    world["lock"].held.add("mail_sync")
    world["graph"].receive(subject="[P00015] a", sender=SUPPLIER)
    report = await world["runner"].run()
    assert report.status == "skipped_locked" and report.fetched == 0
    assert world["director"].events == []


async def test_failing_message_keeps_delta_and_retries_next_run(world: dict[str, Any]) -> None:
    graph: FakeGraph = world["graph"]
    graph.receive(subject="[P00015] a", sender=SUPPLIER)
    await world["runner"].run()
    graph.receive(subject="[P00015] b", sender=SUPPLIER)
    # the headers call for the new message fails once (delta succeeds first)
    original = graph.get_headers
    calls = {"n": 0}

    async def flaky(message_id: str) -> dict[str, str]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise GraphError("boom")
        return await original(message_id)

    graph.get_headers = flaky  # type: ignore[method-assign]
    report = await world["runner"].run()
    assert report.status == "partial" and report.errors == 1 and report.linked == 0
    assert world["state"].delta_links["me"] == "fake-delta:1"  # not advanced

    report = await world["runner"].run()
    assert report.status == "ok" and report.linked == 1
    assert world["state"].delta_links["me"] == "fake-delta:2"


async def test_system_senders_are_ignored_without_events(world: dict[str, Any]) -> None:
    from mail_sync.ignore import is_ignored

    assert is_ignored(
        "Account-Security-NoReply@accountprotection.microsoft.com",
        ["accountprotection.microsoft.com"],
    )
    assert is_ignored("member_services@outlook.com", ["member_services@outlook.com"])
    assert not is_ignored("ventas@outlook.com", ["member_services@outlook.com"])
    assert is_ignored("x@mail.microsoftonline.com", ["*@microsoftonline.com"])
    assert not is_ignored(None, ["anything"])

    graph: FakeGraph = world["graph"]
    graph.receive(
        subject="New sign-in detected",
        sender="account-security-noreply@accountprotection.microsoft.com",
    )
    graph.receive(subject="[P00015] quote", sender=SUPPLIER)
    report = await world["runner"].run()
    assert report.ignored == 1 and report.linked == 1
    assert len(world["director"].events) == 1
