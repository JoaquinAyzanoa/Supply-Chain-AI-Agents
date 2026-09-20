"""Learning from decisions.

* ``FeedbackRecorder`` turns every resolved approval into a ``decision_feedback``
  row: what was proposed (facts and sizes, never mail bodies), what the person
  did (approved as is, edited, rejected), how long it took.
* ``calibrate`` reads those rows and proposes: an autonomy rule where a person
  has approved the same kind of action for the same supplier unchanged many
  times; a wider invoice tolerance where held bills are always approved;
  attention where a kind of proposal is mostly rejected.
* ``CalibrationJob`` runs weekly: backfills feedback for approvals resolved in
  Odoo, then refreshes the open suggestions.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from loguru import logger

from sc_core.infra.db import Database
from sc_core.odoo.models import Approval
from sc_core.odoo.repositories import ApprovalRepo
from sc_core.schema.autonomy import ActionFacts, AutonomyPolicy, AutonomyRule, RuleConditions
from sc_core.schema.base import StrictModel
from sc_core.schema.events import ScheduledTick
from sc_core.shared.errors import NotFound

if TYPE_CHECKING:
    from director.api.approvals import ApprovalsGateway

MIN_UNCHANGED_FOR_RULE = 10
MIN_HELD_BILLS_FOR_TOLERANCE = 5
MAX_TOLERANCE_SUGGESTED = 5.0
MIN_FOR_ATTENTION = 5
ATTENTION_REJECTION_SHARE = 0.5


# --- feedback ------------------------------------------------------------------------------


class DecisionFeedback(StrictModel):
    approval_id: int
    kind: str
    agent: str | None = None
    partner_id: int | None = None
    po_name: str | None = None
    status: str
    edited: bool = False
    edit_fields: list[str] = []
    edit_notes: dict[str, Any] = {}
    reason: str | None = None
    facts: dict[str, Any] = {}
    requested_at: datetime | None = None
    resolved_at: datetime | None = None
    seconds_to_decide: float | None = None
    resolved_by: str | None = None
    via: str | None = None

    @property
    def unchanged(self) -> bool:
        return self.status == "approved" and not self.edited


def edit_summary(
    kind: str, payload: dict[str, Any], details: dict[str, Any] | None
) -> tuple[bool, list[str], dict[str, Any]]:
    """Whether the person changed the proposal, which fields, and by how much (numbers only)."""
    details = details or {}
    fields: list[str] = []
    notes: dict[str, Any] = {}
    if kind == "send_email":
        fields = [f for f in ("subject", "html_body") if details.get(f)]
    elif kind == "po_change":
        accepted = details.get("accepted_line_ids")
        proposed = [c for c in payload.get("changes") or [] if not c.get("needs_review")]
        if accepted is not None and len(accepted) < len(proposed):
            fields.append("accepted_line_ids")
            notes["dropped_lines"] = len(proposed) - len(accepted)
    elif kind == "planning_run":
        accepted = details.get("accepted_line_ids")
        proposed = payload.get("lines") or []
        if accepted is not None and len(accepted) < len(proposed):
            fields.append("accepted_line_ids")
            notes["dropped_lines"] = len(proposed) - len(accepted)
        edits = details.get("edits") or {}
        if edits:
            fields.append("edits")
            notes["lines_edited"] = len(edits)
        if details.get("params"):
            fields.append("params")
            notes["params_set"] = len(details["params"])
    return bool(fields), fields, notes


def payload_notes(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Sizes worth learning from, taken from the proposal itself."""
    notes: dict[str, Any] = {}
    if kind == "vendor_bill":
        variances: list[float] = []
        for line in payload.get("lines") or []:
            po_price, inv_price = line.get("po_price"), line.get("invoice_price")
            if po_price and inv_price is not None:
                variances.append(abs(float(inv_price) - float(po_price)) / float(po_price) * 100)
        if variances:
            notes["max_variance_pct"] = round(max(variances), 2)
        notes["verdict"] = payload.get("verdict")
    return notes


@runtime_checkable
class FeedbackStore(Protocol):
    async def record(self, row: DecisionFeedback) -> None: ...

    async def has(self, approval_id: int) -> bool: ...

    async def recent(self, *, since: datetime, limit: int = 5000) -> list[DecisionFeedback]: ...


class PostgresFeedbackStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, row: DecisionFeedback) -> None:
        await self._db.execute(
            "INSERT INTO decision_feedback (approval_id, kind, agent, partner_id, po_name, status, "
            "edited, edit_fields, edit_notes, reason, facts, requested_at, resolved_at, "
            "seconds_to_decide, resolved_by, via) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
            "%s::jsonb, %s, %s::jsonb, %s, %s, %s, %s, %s) "
            "ON CONFLICT (approval_id) DO UPDATE SET status = EXCLUDED.status, "
            "edited = EXCLUDED.edited, edit_fields = EXCLUDED.edit_fields, "
            "edit_notes = EXCLUDED.edit_notes, reason = EXCLUDED.reason, "
            "resolved_at = EXCLUDED.resolved_at, seconds_to_decide = EXCLUDED.seconds_to_decide, "
            "resolved_by = EXCLUDED.resolved_by, via = EXCLUDED.via",
            (
                row.approval_id,
                row.kind,
                row.agent,
                row.partner_id,
                row.po_name,
                row.status,
                row.edited,
                json.dumps(row.edit_fields),
                json.dumps(row.edit_notes, default=str),
                row.reason,
                json.dumps(row.facts, default=str),
                row.requested_at,
                row.resolved_at,
                row.seconds_to_decide,
                row.resolved_by,
                row.via,
            ),
        )

    async def has(self, approval_id: int) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 AS x FROM decision_feedback WHERE approval_id = %s", (approval_id,)
        )
        return row is not None

    async def recent(self, *, since: datetime, limit: int = 5000) -> list[DecisionFeedback]:
        rows = await self._db.fetch_all(
            "SELECT * FROM decision_feedback WHERE resolved_at >= %s "
            "ORDER BY resolved_at DESC LIMIT %s",
            (since, limit),
        )
        return [_feedback_row(r) for r in rows]


def _js(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _feedback_row(row: dict[str, Any]) -> DecisionFeedback:
    return DecisionFeedback(
        approval_id=int(row["approval_id"]),
        kind=str(row["kind"]),
        agent=row.get("agent"),
        partner_id=row.get("partner_id"),
        po_name=row.get("po_name"),
        status=str(row["status"]),
        edited=bool(row.get("edited")),
        edit_fields=[str(f) for f in _js(row.get("edit_fields")) or []],
        edit_notes=_js(row.get("edit_notes")) or {},
        reason=row.get("reason"),
        facts=_js(row.get("facts")) or {},
        requested_at=row.get("requested_at"),
        resolved_at=row.get("resolved_at"),
        seconds_to_decide=row.get("seconds_to_decide"),
        resolved_by=row.get("resolved_by"),
        via=row.get("via"),
    )


class MemoryFeedbackStore:
    def __init__(self) -> None:
        self.rows: dict[int, DecisionFeedback] = {}

    async def record(self, row: DecisionFeedback) -> None:
        self.rows[row.approval_id] = row

    async def has(self, approval_id: int) -> bool:
        return approval_id in self.rows

    async def recent(self, *, since: datetime, limit: int = 5000) -> list[DecisionFeedback]:
        rows = [r for r in self.rows.values() if r.resolved_at is None or r.resolved_at >= since]
        rows.sort(key=lambda r: r.resolved_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return rows[:limit]


def feedback_from(
    approval: Approval,
    *,
    status: str,
    by: str | None,
    via: str | None,
    reason: str | None,
    details: dict[str, Any] | None,
    resolved_at: datetime | None,
) -> DecisionFeedback:
    payload = ApprovalRepo.payload_of(approval)
    facts = dict(payload.get("facts") or {})
    edited, fields, notes = edit_summary(approval.kind, payload, details)
    notes.update(payload_notes(approval.kind, payload))
    requested = approval.create_date
    resolved = resolved_at or datetime.now(UTC)
    seconds = None
    if requested is not None:
        req = requested if requested.tzinfo else requested.replace(tzinfo=UTC)
        res = resolved if resolved.tzinfo else resolved.replace(tzinfo=UTC)
        seconds = max((res - req).total_seconds(), 0.0)
    return DecisionFeedback(
        approval_id=approval.id,
        kind=approval.kind,
        agent=approval.requested_by,
        partner_id=facts.get("partner_id"),
        po_name=approval.po_id.name if approval.po_id else payload.get("po_name"),
        status=status,
        edited=edited,
        edit_fields=fields,
        edit_notes=notes,
        reason=reason,
        facts=facts,
        requested_at=requested,
        resolved_at=resolved,
        seconds_to_decide=seconds,
        resolved_by=by,
        via=via,
    )


class FeedbackRecorder:
    """Writes the signal at the two places a decision arrives: the API and Odoo."""

    def __init__(self, approvals: ApprovalsGateway, store: FeedbackStore) -> None:
        self._approvals = approvals
        self._store = store

    async def record_resolution(
        self,
        approval: Approval,
        *,
        status: str,
        by: str | None,
        via: str,
        reason: str | None,
        details: dict[str, Any] | None,
    ) -> DecisionFeedback:
        row = feedback_from(
            approval,
            status=status,
            by=by,
            via=via,
            reason=reason,
            details=details,
            resolved_at=datetime.now(UTC),
        )
        await self._store.record(row)
        return row

    async def record_from_odoo(self, approval_id: int, *, status: str, by: str | None) -> None:
        """A decision made in Odoo: read the approval back for its edits and reason."""
        try:
            approval = await self._approvals.get(approval_id)
        except NotFound:
            return
        details = json.loads(approval.details_json) if approval.details_json else None
        await self._store.record(
            feedback_from(
                approval,
                status=status,
                by=by or approval.resolved_by_name,
                via="odoo",
                reason=approval.reason,
                details=details,
                resolved_at=approval.resolved_at or datetime.now(UTC),
            )
        )

    async def backfill(self) -> int:
        """Feedback for every resolved approval not recorded yet (older ones, Odoo ones)."""
        rows = await self._approvals.list(status="all", kind=None, po_name=None)
        added = 0
        for approval in rows:
            if approval.status == "pending" or await self._store.has(approval.id):
                continue
            details = json.loads(approval.details_json) if approval.details_json else None
            await self._store.record(
                feedback_from(
                    approval,
                    status=approval.status,
                    by=approval.resolved_by_name,
                    via=approval.resolved_via,
                    reason=approval.reason,
                    details=details,
                    resolved_at=approval.resolved_at or approval.create_date,
                )
            )
            added += 1
        return added


# --- statistics ---------------------------------------------------------------------------


class FeedbackStats(StrictModel):
    kind: str
    partner_id: int | None = None
    partner_name: str | None = None
    n: int
    unchanged: int
    edited: int
    rejected: int
    expired: int
    median_minutes: float | None = None


def group_stats(rows: list[DecisionFeedback], *, by_partner: bool) -> list[FeedbackStats]:
    groups: dict[tuple[str, int | None], list[DecisionFeedback]] = {}
    for row in rows:
        key = (row.kind, row.partner_id if by_partner else None)
        groups.setdefault(key, []).append(row)
    out: list[FeedbackStats] = []
    for (kind, partner_id), items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0][0])):
        times = [r.seconds_to_decide / 60 for r in items if r.seconds_to_decide is not None]
        out.append(
            FeedbackStats(
                kind=kind,
                partner_id=partner_id,
                partner_name=next(
                    (r.facts.get("partner_name") for r in items if r.facts.get("partner_name")),
                    None,
                ),
                n=len(items),
                unchanged=sum(1 for r in items if r.unchanged),
                edited=sum(1 for r in items if r.status == "approved" and r.edited),
                rejected=sum(1 for r in items if r.status == "rejected"),
                expired=sum(1 for r in items if r.status == "expired"),
                median_minutes=round(statistics.median(times), 1) if times else None,
            )
        )
    return out


# --- suggestions ---------------------------------------------------------------------------

SuggestionKind = Literal["autonomy_rule", "setting", "attention"]


class Suggestion(StrictModel):
    id: int = 0
    key: str
    kind: SuggestionKind
    title: str
    detail: str
    evidence: dict[str, Any] = {}
    proposal: dict[str, Any] = {}
    status: str = "open"
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None


@runtime_checkable
class SuggestionStore(Protocol):
    async def upsert_open(self, suggestion: Suggestion) -> Suggestion: ...

    async def open(self) -> list[Suggestion]: ...

    async def get(self, suggestion_id: int) -> Suggestion | None: ...

    async def close(self, suggestion_id: int, *, status: str, by: str) -> Suggestion: ...


class PostgresSuggestionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_open(self, suggestion: Suggestion) -> Suggestion:
        row = await self._db.fetch_one(
            "INSERT INTO calibration_suggestions (key, kind, title, detail, evidence, proposal) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb) "
            "ON CONFLICT (key) WHERE status = 'open' DO UPDATE SET title = EXCLUDED.title, "
            "detail = EXCLUDED.detail, evidence = EXCLUDED.evidence, proposal = EXCLUDED.proposal "
            "RETURNING *",
            (
                suggestion.key,
                suggestion.kind,
                suggestion.title,
                suggestion.detail,
                json.dumps(suggestion.evidence, default=str),
                json.dumps(suggestion.proposal, default=str),
            ),
        )
        assert row is not None
        return _suggestion_row(row)

    async def open(self) -> list[Suggestion]:
        rows = await self._db.fetch_all(
            "SELECT * FROM calibration_suggestions WHERE status = 'open' ORDER BY created_at DESC"
        )
        return [_suggestion_row(r) for r in rows]

    async def get(self, suggestion_id: int) -> Suggestion | None:
        row = await self._db.fetch_one(
            "SELECT * FROM calibration_suggestions WHERE id = %s", (suggestion_id,)
        )
        return _suggestion_row(row) if row else None

    async def close(self, suggestion_id: int, *, status: str, by: str) -> Suggestion:
        row = await self._db.fetch_one(
            "UPDATE calibration_suggestions SET status = %s, resolved_at = now(), resolved_by = %s "
            "WHERE id = %s RETURNING *",
            (status, by, suggestion_id),
        )
        if row is None:
            raise NotFound(f"suggestion {suggestion_id} not found")
        return _suggestion_row(row)


def _suggestion_row(row: dict[str, Any]) -> Suggestion:
    return Suggestion(
        id=int(row["id"]),
        key=str(row["key"]),
        kind=str(row["kind"]),  # type: ignore[arg-type]
        title=str(row["title"]),
        detail=str(row["detail"]),
        evidence=_js(row.get("evidence")) or {},
        proposal=_js(row.get("proposal")) or {},
        status=str(row.get("status") or "open"),
        created_at=row.get("created_at"),
        resolved_at=row.get("resolved_at"),
        resolved_by=row.get("resolved_by"),
    )


class MemorySuggestionStore:
    def __init__(self) -> None:
        self.rows: dict[int, Suggestion] = {}

    async def upsert_open(self, suggestion: Suggestion) -> Suggestion:
        for existing in self.rows.values():
            if existing.key == suggestion.key and existing.status == "open":
                updated = existing.model_copy(
                    update={
                        "title": suggestion.title,
                        "detail": suggestion.detail,
                        "evidence": suggestion.evidence,
                        "proposal": suggestion.proposal,
                    }
                )
                self.rows[existing.id] = updated
                return updated
        new_id = max(self.rows, default=0) + 1
        created = suggestion.model_copy(update={"id": new_id, "created_at": datetime.now(UTC)})
        self.rows[new_id] = created
        return created

    async def open(self) -> list[Suggestion]:
        return [s for s in self.rows.values() if s.status == "open"]

    async def get(self, suggestion_id: int) -> Suggestion | None:
        return self.rows.get(suggestion_id)

    async def close(self, suggestion_id: int, *, status: str, by: str) -> Suggestion:
        if suggestion_id not in self.rows:
            raise NotFound(f"suggestion {suggestion_id} not found")
        closed = self.rows[suggestion_id].model_copy(
            update={"status": status, "resolved_at": datetime.now(UTC), "resolved_by": by}
        )
        self.rows[suggestion_id] = closed
        return closed


def calibrate(
    rows: list[DecisionFeedback],
    policy: AutonomyPolicy,
    *,
    days: int,
    min_unchanged: int = MIN_UNCHANGED_FOR_RULE,
) -> list[Suggestion]:
    """What the decisions of the last ``days`` say the policy and the tolerances could be."""
    out: list[Suggestion] = []

    # 1. the same kind of action for the same supplier, always approved unchanged -> a rule
    for group in group_stats(rows, by_partner=True):
        if group.partner_id is None or group.n < min_unchanged:
            continue
        if group.unchanged != group.n:
            continue
        facts = ActionFacts(partner_id=group.partner_id)
        if policy.decide(group.kind, facts).automatic:
            continue  # already runs alone
        name = group.partner_name or f"supplier {group.partner_id}"
        rule = AutonomyRule(
            id=f"suggested-{group.kind.replace('_', '-')}-{group.partner_id}",
            kind=group.kind,
            when=RuleConditions(partner_ids=[group.partner_id]),
            level="auto_notice",
            note=f"{group.n} {group.kind} approvals for {name} in {days} days, all unchanged",
        )
        out.append(
            Suggestion(
                key=f"autonomy:{group.kind}:{group.partner_id}",
                kind="autonomy_rule",
                title=f"Let {group.kind.replace('_', ' ')} for {name} run alone",
                detail=(
                    f"You approved {group.n} of {group.n} {group.kind.replace('_', ' ')} proposals "
                    f"for {name} unchanged in the last {days} days. A revertible rule would save "
                    f"those decisions; you keep the feed and 24 hours to undo any of them."
                ),
                evidence={
                    "n": group.n,
                    "days": days,
                    "kind": group.kind,
                    "partner_id": group.partner_id,
                },
                proposal={"rule": rule.model_dump(mode="json")},
            )
        )

    # 2. held invoices always approved -> a wider price tolerance
    held = [
        r
        for r in rows
        if r.kind == "vendor_bill"
        and r.edit_notes.get("verdict") == "hold"
        and r.edit_notes.get("max_variance_pct") is not None
    ]
    if len(held) >= MIN_HELD_BILLS_FOR_TOLERANCE and all(r.status == "approved" for r in held):
        worst = max(float(r.edit_notes["max_variance_pct"]) for r in held)
        if 0 < worst <= MAX_TOLERANCE_SUGGESTED:
            tolerance = math.ceil(worst * 10) / 10
            out.append(
                Suggestion(
                    key="setting:invoice_price_tolerance_pct",
                    kind="setting",
                    title=f"Raise the invoice price tolerance to {tolerance:g} %",
                    detail=(
                        f"{len(held)} invoices were held for a price variance and every one was "
                        f"recorded as proposed; the largest variance was {worst:.2f} %. With the "
                        f"tolerance at {tolerance:g} % they would have matched."
                    ),
                    evidence={"n": len(held), "max_variance_pct": worst, "days": days},
                    proposal={"setting": "invoice_price_tolerance_pct", "value": tolerance},
                )
            )

    # 3. a kind of proposal mostly rejected -> attention
    for group in group_stats(rows, by_partner=True):
        if group.n < MIN_FOR_ATTENTION or group.rejected / group.n < ATTENTION_REJECTION_SHARE:
            continue
        name = group.partner_name or (
            f"supplier {group.partner_id}" if group.partner_id else "all suppliers"
        )
        out.append(
            Suggestion(
                key=f"attention:{group.kind}:{group.partner_id}",
                kind="attention",
                title=f"{group.kind.replace('_', ' ').capitalize()} for {name} is mostly rejected",
                detail=(
                    f"{group.rejected} of {group.n} {group.kind.replace('_', ' ')} proposals for "
                    f"{name} were rejected in the last {days} days. The agent may be missing a "
                    f"rule of thumb the buyers apply; read the reasons on the rejected approvals."
                ),
                evidence={"n": group.n, "rejected": group.rejected, "days": days},
            )
        )
    return out


class CalibrationJob:
    """Weekly: feedback for what was decided in Odoo, then fresh suggestions."""

    def __init__(
        self,
        *,
        recorder: FeedbackRecorder,
        feedback: FeedbackStore,
        suggestions: SuggestionStore,
        policy: Callable[[], Awaitable[AutonomyPolicy]],
        days: int = 90,
    ) -> None:
        self._recorder = recorder
        self._feedback = feedback
        self._suggestions = suggestions
        self._policy = policy
        self._days = days

    async def run(self, job_id: str, tick: ScheduledTick) -> dict[str, Any]:
        if job_id != "calibration":
            return {"job": job_id, "status": "not_implemented"}
        backfilled = await self._recorder.backfill()
        since = datetime.now(UTC) - timedelta(days=self._days)
        rows = await self._feedback.recent(since=since)
        proposals = calibrate(rows, await self._policy(), days=self._days)
        for suggestion in proposals:
            await self._suggestions.upsert_open(suggestion)
        logger.bind(run_id=tick.run_id, rows=len(rows), suggestions=len(proposals)).info(
            "calibration done"
        )
        return {
            "job": job_id,
            "status": "ok",
            "backfilled": backfilled,
            "rows": len(rows),
            "suggestions": [s.key for s in proposals],
        }
