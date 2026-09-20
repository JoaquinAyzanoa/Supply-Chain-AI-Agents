"""``planning_runs`` and ``planning_lines`` (migration 005): numbers only.

One row per run with its status and totals; one row per line with the
inputs that produced its outputs, so any quantity can be recomputed, and
what was finally applied.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.schema.planning import ReplenishmentLine, ReplenishmentProposal
from sc_core.shared.time import utc_now

INPUT_FIELDS = (
    "on_hand",
    "reserved",
    "incoming",
    "position",
    "forecast_daily",
    "forecast_method",
    "sigma_daily",
    "wape",
    "mape",
    "history_periods",
    "lead_time_days",
    "sigma_lead_time_days",
    "service_level",
    "review_period_days",
    "abc_class",
    "current_min",
    "current_max",
    "supplier_id",
    "supplier_name",
    "unit_price",
    "currency",
    "moq",
)
OUTPUT_FIELDS = (
    "ss",
    "rop",
    "order_up_to",
    "coverage_days",
    "proposed_min",
    "proposed_max",
    "order_qty",
)


@runtime_checkable
class RunStore(Protocol):
    async def save_proposal(
        self, proposal: ReplenishmentProposal, *, case_id: str, kind: str, status: str
    ) -> None: ...

    async def set_status(
        self, run_id: str, status: str, *, approval_id: int | None = None
    ) -> None: ...

    async def mark_applied(
        self, run_id: str, applied: dict[str, dict[str, Any]], *, accepted: set[str]
    ) -> None:
        """``applied`` maps line ids to what was written; ``accepted`` the ids a person accepted."""
        ...

    async def latest_run_id(self, *, kind: str, as_of: date) -> str | None: ...


class MemoryRunStore:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.lines: dict[str, dict[str, Any]] = {}

    async def save_proposal(
        self, proposal: ReplenishmentProposal, *, case_id: str, kind: str, status: str
    ) -> None:
        self.runs[proposal.run_id] = {
            "run_id": proposal.run_id,
            "case_id": case_id,
            "kind": kind,
            "as_of": proposal.as_of,
            "warehouse_id": proposal.warehouse_id,
            "status": status,
            "approval_id": None,
            "summary": proposal.summary,
            "totals": dict(proposal.totals),
        }
        for line in proposal.lines:
            self.lines[line.line_id] = {
                **line_row(line),
                "run_id": proposal.run_id,
                "accepted": None,
                "applied": None,
            }

    async def set_status(self, run_id: str, status: str, *, approval_id: int | None = None) -> None:
        self.runs[run_id]["status"] = status
        if approval_id is not None:
            self.runs[run_id]["approval_id"] = approval_id

    async def mark_applied(
        self, run_id: str, applied: dict[str, dict[str, Any]], *, accepted: set[str]
    ) -> None:
        for line_id, row in self.lines.items():
            if row["run_id"] != run_id:
                continue
            row["accepted"] = line_id in accepted
            if line_id in applied:
                row["applied"] = applied[line_id]
                row["applied_at"] = utc_now()

    async def latest_run_id(self, *, kind: str, as_of: date) -> str | None:
        matches = [r for r in self.runs.values() if r["kind"] == kind and r["as_of"] == as_of]
        return matches[-1]["run_id"] if matches else None


class PostgresRunStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_proposal(
        self, proposal: ReplenishmentProposal, *, case_id: str, kind: str, status: str
    ) -> None:
        await self._db.execute(
            "INSERT INTO planning_runs (run_id, case_id, kind, as_of, warehouse_id, status, "
            "summary, totals) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
            "ON CONFLICT (run_id) DO UPDATE SET status = EXCLUDED.status, "
            "summary = EXCLUDED.summary, totals = EXCLUDED.totals, updated_at = now()",
            (
                proposal.run_id,
                case_id,
                kind,
                proposal.as_of,
                proposal.warehouse_id,
                status,
                proposal.summary,
                json.dumps(proposal.totals),
            ),
        )
        for line in proposal.lines:
            row = line_row(line)
            await self._db.execute(
                "INSERT INTO planning_lines (run_id, line_id, product_id, product_ref, "
                "warehouse_id, inputs, outputs, action, exception, explanation) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s) "
                "ON CONFLICT (run_id, product_id) DO UPDATE SET inputs = EXCLUDED.inputs, "
                "outputs = EXCLUDED.outputs, action = EXCLUDED.action, "
                "exception = EXCLUDED.exception, explanation = EXCLUDED.explanation",
                (
                    proposal.run_id,
                    line.line_id,
                    line.product_id,
                    line.product_ref,
                    line.warehouse_id,
                    json.dumps(row["inputs"]),
                    json.dumps(row["outputs"]),
                    line.action,
                    line.exception,
                    line.explanation,
                ),
            )

    async def set_status(self, run_id: str, status: str, *, approval_id: int | None = None) -> None:
        await self._db.execute(
            "UPDATE planning_runs SET status = %s, approval_id = coalesce(%s, approval_id), "
            "updated_at = now() WHERE run_id = %s",
            (status, approval_id, run_id),
        )

    async def mark_applied(
        self, run_id: str, applied: dict[str, dict[str, Any]], *, accepted: set[str]
    ) -> None:
        await self._db.execute(
            "UPDATE planning_lines SET accepted = (line_id = ANY(%s)) WHERE run_id = %s",
            (list(accepted), run_id),
        )
        for line_id, payload in applied.items():
            await self._db.execute(
                "UPDATE planning_lines SET applied = %s::jsonb, applied_at = now() "
                "WHERE run_id = %s AND line_id = %s",
                (json.dumps(payload, default=str), run_id, line_id),
            )

    async def latest_run_id(self, *, kind: str, as_of: date) -> str | None:
        row = await self._db.fetch_one(
            "SELECT run_id FROM planning_runs WHERE kind = %s AND as_of = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (kind, as_of),
        )
        return str(row["run_id"]) if row else None


def line_row(line: ReplenishmentLine) -> dict[str, Any]:
    data = line.model_dump(mode="json")
    return {
        "line_id": line.line_id,
        "product_id": line.product_id,
        "product_ref": line.product_ref,
        "inputs": {k: data[k] for k in INPUT_FIELDS},
        "outputs": {k: data[k] for k in OUTPUT_FIELDS},
        "action": line.action,
        "exception": line.exception,
        "explanation": line.explanation,
    }
