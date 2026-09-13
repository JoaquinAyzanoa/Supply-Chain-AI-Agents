"""What ran without a person, and how the policy that allowed it is changed.

* ``AutoActionsStore`` reads the ``auto_actions`` rows the agents' approval
  gateways write, marks one reverted.
* ``Reverter`` applies the inverse write an ``auto_notice`` row carries
  (today: the previous planned dates of an order change).
* ``AutonomyChanges`` turns a policy edit into a versioned settings save, at
  once when it only lowers autonomy, or through an ``autonomy_change``
  approval a second person confirms when it widens it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from loguru import logger

from sc_core.app.realtime import Realtime
from sc_core.infra.db import Database
from sc_core.infra.runtime_settings import RuntimeSettingsReader
from sc_core.odoo.models import Approval
from sc_core.odoo.repositories import ApprovalRepo, PurchaseOrderRepo
from sc_core.schema.autonomy import AutonomyPolicy
from sc_core.schema.base import StrictModel
from sc_core.schema.runtime_settings import RuntimeSettings
from sc_core.shared.errors import NotFound, ScError, ValidationFailed

if TYPE_CHECKING:  # the approvals API imports this module: names only, no cycle at runtime
    from director.api.approvals import ApprovalsGateway
    from director.api.settings import RuntimeSettingsStore, SettingsVersion

REQUESTER = "director"


class AutoAction(StrictModel):
    id: int
    created_at: datetime
    case_id: str | None = None
    run_id: str | None = None
    agent: str
    kind: str
    level: str
    rule_id: str | None = None
    summary: str
    po_id: int | None = None
    po_name: str | None = None
    partner_id: int | None = None
    payload: dict[str, Any] = {}
    revert: dict[str, Any] | None = None
    revert_until: datetime | None = None
    reverted_at: datetime | None = None
    reverted_by: str | None = None

    def revertible_at(self, now: datetime) -> bool:
        return (
            self.revert is not None
            and self.reverted_at is None
            and self.revert_until is not None
            and now <= self.revert_until
        )


@runtime_checkable
class AutoActionsStore(Protocol):
    async def recent(self, *, since: datetime, limit: int = 200) -> list[AutoAction]: ...

    async def for_case(self, case_id: str) -> list[AutoAction]: ...

    async def get(self, action_id: int) -> AutoAction | None: ...

    async def mark_reverted(self, action_id: int, *, by: str) -> AutoAction: ...


class PostgresAutoActionsStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def recent(self, *, since: datetime, limit: int = 200) -> list[AutoAction]:
        rows = await self._db.fetch_all(
            "SELECT * FROM auto_actions WHERE created_at >= %s ORDER BY created_at DESC LIMIT %s",
            (since, limit),
        )
        return [_row(r) for r in rows]

    async def for_case(self, case_id: str) -> list[AutoAction]:
        rows = await self._db.fetch_all(
            "SELECT * FROM auto_actions WHERE case_id = %s ORDER BY created_at ASC", (case_id,)
        )
        return [_row(r) for r in rows]

    async def get(self, action_id: int) -> AutoAction | None:
        row = await self._db.fetch_one("SELECT * FROM auto_actions WHERE id = %s", (action_id,))
        return _row(row) if row else None

    async def mark_reverted(self, action_id: int, *, by: str) -> AutoAction:
        row = await self._db.fetch_one(
            "UPDATE auto_actions SET reverted_at = now(), reverted_by = %s WHERE id = %s "
            "RETURNING *",
            (by, action_id),
        )
        if row is None:
            raise NotFound(f"automatic action {action_id} not found")
        return _row(row)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _row(row: dict[str, Any]) -> AutoAction:
    return AutoAction(
        id=int(row["id"]),
        created_at=row["created_at"],
        case_id=row.get("case_id"),
        run_id=row.get("run_id"),
        agent=str(row["agent"]),
        kind=str(row["kind"]),
        level=str(row["level"]),
        rule_id=row.get("rule_id"),
        summary=str(row["summary"]),
        po_id=row.get("po_id"),
        po_name=row.get("po_name"),
        partner_id=row.get("partner_id"),
        payload=_json(row.get("payload")) or {},
        revert=_json(row.get("revert")),
        revert_until=row.get("revert_until"),
        reverted_at=row.get("reverted_at"),
        reverted_by=row.get("reverted_by"),
    )


# --- revert ------------------------------------------------------------------------------


@runtime_checkable
class Reverter(Protocol):
    async def revert(self, action: AutoAction, *, by: str) -> str:
        """Apply the inverse write; returns the note left on the record."""
        ...


class OdooReverter:
    """The inverse writes we know how to make. An email cannot be unsent: a
    ``send_email`` row carries no revert and never reaches here."""

    def __init__(self, orders: PurchaseOrderRepo) -> None:
        self._orders = orders

    async def revert(self, action: AutoAction, *, by: str) -> str:
        if action.kind != "po_change" or not action.revert:
            raise ValidationFailed(f"an automatic {action.kind} cannot be reverted")
        po_id = int(action.revert.get("po_id") or action.po_id or 0)
        lines = action.revert.get("lines") or []
        if not po_id or not lines:
            raise ValidationFailed("nothing to revert on this action")
        restored: list[str] = []
        for line in lines:
            if line.get("field") != "date_planned" or not line.get("before"):
                continue
            before = datetime.fromisoformat(str(line["before"]))
            await self._orders.set_line_date_planned(
                int(line["line_id"]), before, source="supplier", run_id=f"revert_{action.id}"
            )
            restored.append(f"line {line['line_id']} back to {before.date().isoformat()}")
        note = (
            f"Automatic change #{action.id} reverted by {by} "
            f"(rule {action.rule_id or '-'}): " + "; ".join(restored)
        )
        await self._orders.post_note(po_id, f"<p>{note}</p>")
        return note


# --- policy changes ----------------------------------------------------------------------


class AutonomyChanges:
    """Save a policy edit, or ask a second person when it widens autonomy."""

    def __init__(
        self,
        *,
        approvals: ApprovalsGateway,
        settings_store: RuntimeSettingsStore,
        reader: RuntimeSettingsReader,
        realtime: Realtime,
    ) -> None:
        self._approvals = approvals
        self._store = settings_store
        self._reader = reader
        self._realtime = realtime

    async def current_settings(self) -> RuntimeSettings:
        version = await self._store.current()
        return version.settings if version else self._reader.defaults

    async def request(
        self,
        policy: AutonomyPolicy,
        *,
        widened: list[str],
        requested_by: str,
        requested_by_name: str,
        note: str | None,
    ) -> Approval:
        """An ``autonomy_change`` approval carrying the whole new policy."""
        summary = f"Widen autonomy: {len(widened)} rule(s) ({', '.join(widened)[:120]})"
        return await self._approvals.create(
            kind="autonomy_change",
            summary=summary[:200],
            payload={
                "policy": policy.model_dump(mode="json"),
                "widened": widened,
                "requested_by": requested_by,
                "requested_by_name": requested_by_name,
                "note": note,
            },
            requested_by=REQUESTER,
            case_id=f"autonomy_{datetime.now(UTC):%Y%m%d%H%M%S}",
        )

    async def save(
        self, policy: AutonomyPolicy, *, changed_by: str, note: str | None
    ) -> SettingsVersion:
        current = await self.current_settings()
        saved = await self._store.save(
            current.model_copy(update={"autonomy": policy}), changed_by=changed_by, note=note
        )
        self._reader.invalidate()
        await self._realtime.publish(
            "settings_changed", {"version": saved.version, "changed_by": changed_by}
        )
        logger.bind(version=saved.version, by=changed_by).info("autonomy policy saved")
        return saved

    async def apply(self, approval: Approval, *, by: str) -> SettingsVersion:
        """An approved ``autonomy_change``: the policy it carries becomes the current one."""
        if approval.kind != "autonomy_change":
            raise ValidationFailed(f"approval {approval.id} is not an autonomy change")
        payload = ApprovalRepo.payload_of(approval)
        policy = AutonomyPolicy.model_validate(payload.get("policy") or {})
        note = payload.get("note") or f"autonomy change approved (approval #{approval.id})"
        return await self.save(policy, changed_by=by, note=str(note))

    async def apply_approval(self, approval_id: int, *, by: str) -> SettingsVersion:
        approval = await self._approvals.get(approval_id)
        return await self.apply(approval, by=by)

    @staticmethod
    def second_person_required(approval: Approval, principal_email: str) -> bool:
        payload = ApprovalRepo.payload_of(approval)
        return str(payload.get("requested_by") or "").lower() == principal_email.lower()


def window_open(action: AutoAction, *, now: datetime | None = None) -> bool:
    return action.revertible_at(now or datetime.now(UTC))


def since_days(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


__all__ = [
    "AutoAction",
    "AutoActionsStore",
    "AutonomyChanges",
    "OdooReverter",
    "PostgresAutoActionsStore",
    "Reverter",
    "ScError",
    "since_days",
    "window_open",
]
