"""The morning briefing (phase 11 S7): what happened, what ran alone, what needs you.

Every section is built from facts the director already holds: cases, the
automatic-actions feed, pending approvals, the risk radar, the follow-up
job's order facts and the playbook runs. The model writes one paragraph
from those facts, in the reader's language, and nothing else: when it does
not answer, the sections stand on their own. One briefing per day is kept
in ``briefings``; it can be emailed to the buyers from the bot mailbox.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from html import escape
from typing import Any, Literal, Protocol, runtime_checkable

from loguru import logger
from pydantic import BaseModel, Field

from director.api.approvals import ApprovalsGateway
from director.api.exceptions import ExceptionsSource, build_board
from director.api.risk import RiskSource
from director.autonomy import AutoActionsStore
from director.escalation import PROMPTS_DIR
from director.playbooks import PlaybookEngine
from director.store import CaseStore
from sc_core.i18n import Language, language_name, t
from sc_core.infra.db import Database
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.infra.settings import LangfuseCfg, Settings
from sc_core.llm.client import ChatCompleter, system, user
from sc_core.llm.structured import StructuredOutputFailed, complete_structured
from sc_core.mail.models import OutboundMessage
from sc_core.mail.protocol import MailClient
from sc_core.odoo.repositories.approval import ApprovalRepo
from sc_core.prompts import get_prompt
from sc_core.schema.base import StrictModel
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import ScError
from sc_core.shared.time import local_today, utc_now

SectionKey = Literal["overnight", "ran_alone", "needs_you", "risks", "late", "playbooks"]
SECTION_KEYS: tuple[SectionKey, ...] = (
    "needs_you",
    "risks",
    "late",
    "overnight",
    "ran_alone",
    "playbooks",
)
MAX_ITEMS = 8


class BriefingItem(StrictModel):
    text: str = Field(max_length=300)
    path: str | None = Field(default=None, description="where to open it in the Control Tower")
    po_name: str | None = None
    amount: float | None = None
    days: int | None = None
    probability: float | None = None


class BriefingSection(StrictModel):
    key: SectionKey
    title: str
    count: int = 0
    items: list[BriefingItem] = []


class Briefing(StrictModel):
    day: date
    language: str = "en"
    since: datetime
    sections: list[BriefingSection] = []
    paragraph: str | None = None
    counts: dict[str, int] = {}
    emailed_to: list[str] = []
    created_at: datetime | None = None


class Paragraph(BaseModel):
    text: str = Field(min_length=1, max_length=1200)


@runtime_checkable
class BriefingStore(Protocol):
    async def save(self, briefing: Briefing) -> Briefing: ...

    async def get(self, day: date) -> Briefing | None: ...

    async def latest(self) -> Briefing | None: ...

    async def recent(self, *, limit: int = 14) -> list[Briefing]: ...


def _briefing(row: dict[str, Any]) -> Briefing:
    sections = row.get("sections") or []
    counts = row.get("counts") or {}
    return Briefing(
        day=row["day"],
        language=str(row.get("language") or "en"),
        since=row["since"],
        sections=[BriefingSection.model_validate(s) for s in sections],
        paragraph=row.get("paragraph"),
        counts={str(k): int(v) for k, v in counts.items()},
        emailed_to=list(row.get("emailed_to") or []),
        created_at=row.get("created_at"),
    )


class PostgresBriefingStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, briefing: Briefing) -> Briefing:
        row = await self._db.fetch_one(
            "INSERT INTO briefings (day, language, since, sections, paragraph, counts, emailed_to) "
            "VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, %s) "
            "ON CONFLICT (day) DO UPDATE SET language = EXCLUDED.language, "
            "since = EXCLUDED.since, sections = EXCLUDED.sections, paragraph = EXCLUDED.paragraph, "
            "counts = EXCLUDED.counts, emailed_to = EXCLUDED.emailed_to, created_at = now() "
            "RETURNING *",
            (
                briefing.day,
                briefing.language,
                briefing.since,
                json.dumps([s.model_dump(mode="json") for s in briefing.sections]),
                briefing.paragraph,
                json.dumps(briefing.counts),
                briefing.emailed_to,
            ),
        )
        assert row is not None
        return _briefing(row)

    async def get(self, day: date) -> Briefing | None:
        row = await self._db.fetch_one("SELECT * FROM briefings WHERE day = %s", (day,))
        return _briefing(row) if row else None

    async def latest(self) -> Briefing | None:
        row = await self._db.fetch_one("SELECT * FROM briefings ORDER BY day DESC LIMIT 1")
        return _briefing(row) if row else None

    async def recent(self, *, limit: int = 14) -> list[Briefing]:
        rows = await self._db.fetch_all(
            "SELECT * FROM briefings ORDER BY day DESC LIMIT %s", (limit,)
        )
        return [_briefing(r) for r in rows]


class MemoryBriefingStore:
    def __init__(self) -> None:
        self.rows: dict[date, Briefing] = {}

    async def save(self, briefing: Briefing) -> Briefing:
        saved = briefing.model_copy(update={"created_at": briefing.created_at or utc_now()})
        self.rows[briefing.day] = saved
        return saved

    async def get(self, day: date) -> Briefing | None:
        return self.rows.get(day)

    async def latest(self) -> Briefing | None:
        if not self.rows:
            return None
        return self.rows[max(self.rows)]

    async def recent(self, *, limit: int = 14) -> list[Briefing]:
        return [self.rows[d] for d in sorted(self.rows, reverse=True)[:limit]]


# --- building it ------------------------------------------------------------------------------


class BriefingBuilder:
    """Gathers the day's facts into sections; asks the model for the paragraph only."""

    def __init__(
        self,
        *,
        cases: CaseStore,
        approvals: ApprovalsGateway,
        auto_actions: AutoActionsStore,
        exceptions: ExceptionsSource,
        settings: Settings,
        store: BriefingStore,
        risk: RiskSource | None = None,
        playbooks: PlaybookEngine | None = None,
        chat: ChatCompleter | None = None,
        language: Language = "en",
        langfuse: LangfuseCfg | None = None,
        today: Callable[[], date] = local_today,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._cases = cases
        self._approvals = approvals
        self._auto_actions = auto_actions
        self._exceptions = exceptions
        self._settings = settings
        self._store = store
        self._risk = risk
        self._playbooks = playbooks
        self._chat = chat
        self._language: Language = language
        self._langfuse = langfuse
        self._today = today
        self._now = now

    async def build(self, day: date | None = None) -> Briefing:
        day = day or self._today()
        now = self._now()
        previous = await self._store.latest()
        since = (
            previous.created_at
            if previous is not None and previous.day < day and previous.created_at
            else now - timedelta(hours=24)
        )
        sections = [
            await self._needs_you(now),
            await self._risks(),
            await self._late(day),
            await self._overnight(since),
            await self._ran_alone(since, now),
            await self._playbook_section(),
        ]
        briefing = Briefing(
            day=day,
            language=self._language,
            since=since,
            sections=sections,
            counts={s.key: s.count for s in sections},
        )
        return briefing.model_copy(update={"paragraph": await self._paragraph(briefing)})

    # --- sections -----------------------------------------------------------------------

    async def _needs_you(self, now: datetime) -> BriefingSection:
        pending = await self._approvals.list(status="pending", kind=None, po_name=None)
        return BriefingSection(
            key="needs_you",
            title=t("briefing.section.needs_you", self._language),
            count=len(pending),
            items=await needs_you_items(
                self._approvals, now=now, language=self._language, limit=MAX_ITEMS
            ),
        )

    async def _risks(self) -> BriefingSection:
        items: list[BriefingItem] = []
        count = 0
        if self._risk is not None:
            try:
                report = await self._risk.report()
            except ScError as exc:
                logger.warning("risk radar unavailable for the briefing: {}", exc)
                report = {}
            products = [
                p for p in report.get("products") or [] if float(p.get("p_stockout_30") or 0) >= 0.2
            ]
            count = len(products)
            for p in products[:5]:
                odds = round(100 * float(p.get("p_stockout_30") or 0))
                late = p.get("late_po_names") or []
                text = t(
                    "briefing.risk_product",
                    self._language,
                    ref=p.get("product_ref") or p.get("product_id"),
                    odds=odds,
                )
                if late:
                    text += " · " + t("briefing.risk_late", self._language, po=late[0])
                items.append(
                    BriefingItem(
                        text=text[:300],
                        path="/risk",
                        po_name=late[0] if late else None,
                        probability=float(p.get("p_stockout_30") or 0),
                    )
                )
            for s in report.get("suppliers") or []:
                if int(s.get("overdue_lines") or 0) > 0:
                    items.append(
                        BriefingItem(
                            text=t(
                                "briefing.risk_supplier",
                                self._language,
                                name=s.get("partner_name") or s.get("partner_id"),
                                n=int(s.get("overdue_lines") or 0),
                            ),
                            path="/risk",
                            amount=float(s.get("exposure") or 0) or None,
                        )
                    )
        return BriefingSection(
            key="risks",
            title=t("briefing.section.risks", self._language),
            count=count,
            items=items[:MAX_ITEMS],
        )

    async def _late(self, day: date) -> BriefingSection:
        board = await build_board(
            source=self._exceptions,
            cases=self._cases,
            approvals=self._approvals,
            settings=self._settings,
            today=day,
        )
        items: list[BriefingItem] = []
        for item in sorted(board.late_pos, key=lambda i: -i.days)[:5]:
            items.append(
                BriefingItem(
                    text=t("briefing.late_po", self._language, po=item.title, days=item.days),
                    path=f"/board?po={item.po_name}" if item.po_name else "/exceptions",
                    po_name=item.po_name,
                    days=item.days,
                )
            )
        for item in sorted(board.rfqs_no_reply, key=lambda i: -i.days)[:3]:
            items.append(
                BriefingItem(
                    text=t("briefing.silent_rfq", self._language, po=item.title, days=item.days),
                    path=f"/board?po={item.po_name}" if item.po_name else "/exceptions",
                    po_name=item.po_name,
                    days=item.days,
                )
            )
        return BriefingSection(
            key="late",
            title=t("briefing.section.late", self._language),
            count=len(board.late_pos) + len(board.rfqs_no_reply),
            items=items[:MAX_ITEMS],
        )

    async def _overnight(self, since: datetime) -> BriefingSection:
        cases = await self._cases.list(since=since, limit=200)
        items: list[BriefingItem] = []
        for case in cases:
            if case.status not in ("done", "awaiting_approval", "escalated", "failed"):
                continue
            summary = (case.summary or "").strip()
            if not summary:
                continue
            status_label = t(f"briefing.status.{case.status}", self._language)
            items.append(
                BriefingItem(
                    text=f"{case.code} · {status_label}: {summary}"[:300],
                    path=f"/cases/{case.case_id}",
                    po_name=case.po_name,
                )
            )
        return BriefingSection(
            key="overnight",
            title=t("briefing.section.overnight", self._language),
            count=len(cases),
            items=items[:MAX_ITEMS],
        )

    async def _ran_alone(self, since: datetime, now: datetime) -> BriefingSection:
        actions = await self._auto_actions.recent(since=since, limit=100)
        items: list[BriefingItem] = []
        for action in actions:
            text = action.summary
            if action.rule_id:
                text += " · " + t("briefing.under_rule", self._language, rule=action.rule_id)
            if action.revert_until and action.revert_until > now and action.reverted_at is None:
                text += " · " + t("briefing.revertible", self._language)
            items.append(BriefingItem(text=text[:300], path="/autonomy", po_name=action.po_name))
        return BriefingSection(
            key="ran_alone",
            title=t("briefing.section.ran_alone", self._language),
            count=len(actions),
            items=items[:MAX_ITEMS],
        )

    async def _playbook_section(self) -> BriefingSection:
        items: list[BriefingItem] = []
        total = 0
        if self._playbooks is not None:
            counts = await self._playbooks.counts()
            for name, steps in counts.items():
                active = sum(steps.values())
                if not active:
                    continue
                total += active
                playbook = self._playbooks.playbooks.get(name)
                title = playbook.title if playbook else name
                where = ", ".join(f"{n} × {step}" for step, n in sorted(steps.items()))
                items.append(
                    BriefingItem(
                        text=t(
                            "briefing.playbook", self._language, title=title, n=active, where=where
                        ),
                        path="/playbooks",
                    )
                )
        return BriefingSection(
            key="playbooks",
            title=t("briefing.section.playbooks", self._language),
            count=total,
            items=items[:MAX_ITEMS],
        )

    # --- the paragraph ------------------------------------------------------------------

    async def _paragraph(self, briefing: Briefing) -> str | None:
        if self._chat is None:
            return None
        prompt = get_prompt("briefing_paragraph", local_dir=PROMPTS_DIR, cfg=self._langfuse)
        text = prompt.compile(
            today=briefing.day.isoformat(),
            language=language_name(self._language),
            facts=facts_text(briefing),
        )
        try:
            answer = await complete_structured(
                self._chat,
                [system(text), user("Write the paragraph.")],
                Paragraph,
                name="briefing_paragraph",
                metadata={"day": briefing.day.isoformat()},
            )
        except (ScError, StructuredOutputFailed) as exc:
            logger.warning("briefing paragraph not written: {}", exc)
            return None
        return answer.text.strip()


async def needs_you_items(
    approvals: ApprovalsGateway, *, now: datetime, language: Language, limit: int = MAX_ITEMS
) -> list[BriefingItem]:
    """The pending approvals as items, the costly decision before the old one."""
    items: list[BriefingItem] = []
    for approval in await approvals.list(status="pending", kind=None, po_name=None):
        payload = ApprovalRepo.payload_of(approval)
        facts = payload.get("facts") or {}
        amount = facts.get("amount")
        created = approval.create_date
        days = (now - created).days if created else 0
        label = t(f"briefing.kind.{approval.kind}", language)
        text = f"#{approval.id} {label}: {approval.summary}"
        if amount:
            text += f" · {float(amount):,.0f} {facts.get('currency') or ''}".rstrip()
        if days:
            text += " · " + t("briefing.waiting", language, n=days)
        items.append(
            BriefingItem(
                text=text[:300],
                path=f"/approvals?id={approval.id}",
                po_name=approval.po_id.name if approval.po_id else None,
                amount=float(amount) if amount else None,
                days=days,
            )
        )
    items.sort(key=lambda i: (-(i.amount or 0.0), -(i.days or 0)))
    return items[:limit]


def facts_text(briefing: Briefing) -> str:
    """The sections as plain text: what the model sees, what the email repeats."""
    lines: list[str] = []
    for section in briefing.sections:
        lines.append(f"{section.title} ({section.count}):")
        if not section.items:
            lines.append("- (nothing)")
        for item in section.items:
            lines.append(f"- {item.text}")
    return "\n".join(lines)


def briefing_html(briefing: Briefing, *, control_tower_url: str) -> str:
    """The email body: the paragraph, then every section with links back."""
    base = control_tower_url.rstrip("/")
    title = t("briefing.email_title", briefing.language, day=briefing.day.isoformat())
    open_label = t("briefing.open", briefing.language)
    parts = [f"<h2>{escape(title)}</h2>"]
    if briefing.paragraph:
        parts.append(f"<p>{escape(briefing.paragraph)}</p>")
    for section in briefing.sections:
        parts.append(f"<h3>{escape(section.title)} ({section.count})</h3>")
        if not section.items:
            parts.append(f"<p><i>{escape(t('briefing.nothing', briefing.language))}</i></p>")
            continue
        rows = []
        for item in section.items:
            text = escape(item.text)
            if item.path:
                rows.append(f'<li><a href="{escape(base + item.path)}">{text}</a></li>')
            else:
                rows.append(f"<li>{text}</li>")
        parts.append("<ul>" + "".join(rows) + "</ul>")
    parts.append(f'<p><a href="{escape(base + "/briefing")}">{escape(open_label)}</a></p>')
    return "".join(parts)


class BriefingJob:
    """The 07:30 job: build today's briefing, keep it, email it to the buyers who asked."""

    def __init__(
        self,
        builder: BriefingBuilder,
        store: BriefingStore,
        *,
        runtime: RuntimeSettingsReader | None = None,
        mail: MailClient | None = None,
        control_tower_url: str = "",
    ) -> None:
        self._builder = builder
        self._store = store
        self._runtime = runtime
        self._mail = mail
        self._ui = control_tower_url

    async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]:
        if job_id != "briefing":
            return {"job": job_id, "status": "not_implemented"}
        briefing = await self.build_and_save()
        recipients = await self.recipients()
        sent = await self.email(briefing, recipients) if recipients else []
        return {
            "job": job_id,
            "status": "ok",
            "day": briefing.day.isoformat(),
            "counts": briefing.counts,
            "paragraph": bool(briefing.paragraph),
            "emailed": sent,
        }

    async def build_and_save(self, day: date | None = None) -> Briefing:
        briefing = await self._builder.build(day)
        saved = await self._store.save(briefing)
        logger.bind(day=saved.day.isoformat(), counts=saved.counts).info("briefing built")
        return saved

    async def recipients(self) -> list[str]:
        if self._runtime is None:
            return []
        return list((await self._runtime.current()).briefing_recipients)

    async def email(self, briefing: Briefing, recipients: list[str]) -> list[str]:
        """Send the briefing from the bot mailbox; who got it is kept on the row."""
        if self._mail is None or not recipients:
            return []
        subject = t("briefing.email_title", briefing.language, day=briefing.day.isoformat())
        try:
            await self._mail.send(
                OutboundMessage(
                    to=recipients,
                    subject=subject,
                    html_body=briefing_html(briefing, control_tower_url=self._ui),
                )
            )
        except ScError as exc:
            logger.warning("briefing email failed: {}", exc)
            return []
        emailed = sorted(set(briefing.emailed_to) | set(recipients))
        await self._store.save(briefing.model_copy(update={"emailed_to": emailed}))
        logger.bind(day=briefing.day.isoformat(), to=len(recipients)).info("briefing emailed")
        return recipients


__all__ = [
    "Briefing",
    "BriefingBuilder",
    "BriefingItem",
    "BriefingJob",
    "BriefingSection",
    "BriefingStore",
    "MemoryBriefingStore",
    "PostgresBriefingStore",
    "briefing_html",
    "facts_text",
]
