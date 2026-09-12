"""One sync run: delta from Graph, dedupe, link, record, emit, advance.

Idempotency comes from three places: the ``mail_processed`` table (a
message is handled once even if Graph re-delivers it), the deterministic
event id (a re-emitted event is a duplicate at the director) and the
idempotent ``sc.mail.link`` creation. The delta link only advances when
every message of the page was handled, so a transient failure is retried on
the next run instead of being lost.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from loguru import logger
from pydantic import Field

from mail_sync.linker import Confidence, Linker, LinkerPorts, PoRef
from mail_sync.state import SyncState
from sc_core.a2a.events import EventPublisher
from sc_core.infra import tracing
from sc_core.infra.locks import Lock
from sc_core.infra.settings import MailSyncCfg
from sc_core.mail.errors import DeltaExpired
from sc_core.mail.models import InboundMessage
from sc_core.mail.protocol import MailClient
from sc_core.schema.base import MutableModel
from sc_core.schema.events import InboundMailLinked, InboundMailUnlinked, event_id_for
from sc_core.shared.idempotency import deterministic_id

LOCK_NAME = "mail_sync"
SOURCE = "mail_sync"


class SyncPorts(LinkerPorts, Protocol):
    async def record_link(
        self, po: PoRef, message: InboundMessage, *, case_id: str, confidence: Confidence
    ) -> None: ...


class SyncReport(MutableModel):
    status: Literal["ok", "partial", "skipped_locked"] = "ok"
    fetched: int = 0
    linked: int = 0
    unlinked: int = 0
    skipped: int = 0
    errors: int = 0
    outbox_delivered: int = 0
    full_resync: bool = False
    linked_po_names: list[str] = Field(default_factory=list)


def case_id_for(graph_message_id: str) -> str:
    return deterministic_id("case", "mail", graph_message_id)


class SyncRunner:
    def __init__(
        self,
        *,
        graph: MailClient,
        state: SyncState,
        ports: SyncPorts,
        publisher: EventPublisher,
        lock: Lock,
        cfg: MailSyncCfg,
        mailbox: str,
    ) -> None:
        self._graph = graph
        self._state = state
        self._ports = ports
        self._linker = Linker(ports)
        self._publisher = publisher
        self._lock = lock
        self._cfg = cfg
        self._mailbox = mailbox

    async def run(self) -> SyncReport:
        report = SyncReport()
        async with self._lock.acquire(LOCK_NAME, ttl_seconds=self._cfg.lock_ttl_seconds) as held:
            if not held:
                report.status = "skipped_locked"
                logger.info("mail sync skipped: another run holds the lock")
                return report
            report.outbox_delivered = await self._publisher.flush_outbox()
            page = await self._fetch(report)
            report.fetched = len(page.messages)
            for message in page.messages:
                await self._handle(message, report)
            if report.errors:
                report.status = "partial"
                logger.warning(
                    "mail sync kept the previous delta link: {} message(s) failed", report.errors
                )
            else:
                await self._state.save_delta_link(self._mailbox, page.delta_link, status="ok")
        logger.bind(**report.model_dump()).info("mail sync finished")
        return report

    async def _fetch(self, report: SyncReport) -> Any:
        delta = await self._state.delta_link(self._mailbox)
        try:
            return await self._graph.inbox_delta(delta, page_size=self._cfg.page_size)
        except DeltaExpired:
            logger.warning("delta link expired; full resync (processed table dedupes)")
            report.full_resync = True
            return await self._graph.inbox_delta(None, page_size=self._cfg.page_size)

    async def _handle(self, message: InboundMessage, report: SyncReport) -> None:
        if await self._state.is_processed(message.id):
            report.skipped += 1
            return
        case_id = case_id_for(message.id)
        try:
            with tracing.start_case(
                case_id,
                "inbound_mail",
                input={"graph_message_id": message.id, "has_attachments": message.has_attachments},
            ) as span:
                outcome_name = await self._link_and_emit(message, case_id, report)
                span.update(output={"outcome": outcome_name})
        except Exception:  # noqa: BLE001 - one bad message must not stop the run
            report.errors += 1
            logger.opt(exception=True).bind(case_id=case_id, graph_message_id=message.id).error(
                "message not processed; will retry next run"
            )

    async def _link_and_emit(
        self, message: InboundMessage, case_id: str, report: SyncReport
    ) -> str:
        headers = await self._graph.get_headers(message.id)
        outcome = await self._linker.link(message, headers)
        trace_id = tracing.current_trace_id()
        if outcome.link is not None:
            link = outcome.link
            await self._ports.record_link(
                link.po, message, case_id=case_id, confidence=link.confidence
            )
            await self._publisher.publish(
                InboundMailLinked(
                    event_id=event_id_for("inbound_mail.linked", message.id),
                    source=SOURCE,
                    case_id=case_id,
                    trace_id=trace_id,
                    po_id=link.po.id,
                    po_name=link.po.name,
                    graph_message_id=message.id,
                    conversation_id=message.conversation_id,
                    internet_message_id=message.internet_message_id,
                    has_attachments=message.has_attachments,
                    confidence=link.confidence,
                    rule=link.rule,
                )
            )
            await self._state.mark_processed(
                message.id, outcome="linked", po_name=link.po.name, case_id=case_id
            )
            report.linked += 1
            report.linked_po_names.append(link.po.name)
            logger.bind(case_id=case_id, po_name=link.po.name, rule=link.rule).info("mail linked")
            return "linked"

        await self._publisher.publish(
            InboundMailUnlinked(
                event_id=event_id_for("inbound_mail.unlinked", message.id),
                source=SOURCE,
                case_id=case_id,
                trace_id=trace_id,
                graph_message_id=message.id,
                conversation_id=message.conversation_id,
                sender_address=message.sender.normalized if message.sender else None,
                partner_id=outcome.hint.partner_id,
                open_po_names=outcome.hint.open_po_names,
                has_attachments=message.has_attachments,
            )
        )
        await self._state.mark_processed(message.id, outcome="unlinked", case_id=case_id)
        report.unlinked += 1
        logger.bind(case_id=case_id, partner_id=outcome.hint.partner_id).info("mail unlinked")
        return "unlinked"
