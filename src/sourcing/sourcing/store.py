"""Rounds, invitations and negotiations in the app database (migration 012)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from sc_core.infra.db import Database
from sc_core.shared.errors import NotFound
from sourcing.models import BasketLine, Negotiation, Round, RoundRfq

ACTIVE: tuple[str, ...] = ("open", "comparing", "awaiting_award", "rejected")


@runtime_checkable
class RoundStore(Protocol):
    async def create_round(
        self,
        *,
        case_id: str,
        basket: list[BasketLine],
        deadline: datetime,
        source_po_name: str | None,
        incumbent_partner_id: int | None,
        created_by: str | None,
    ) -> Round: ...

    async def get_round(self, round_id: int) -> Round | None: ...

    async def round_for_case(self, case_id: str) -> Round | None: ...

    async def open_round_for_po(self, po_name: str) -> Round | None: ...

    async def update_round(self, round_id: int, **fields: Any) -> Round: ...

    async def set_rfqs(self, round_id: int, rfqs: list[RoundRfq]) -> None: ...

    async def update_rfq(self, round_id: int, partner_id: int, **fields: Any) -> None: ...

    async def rounds(
        self, *, status: str | None = None, partner_id: int | None = None, limit: int = 100
    ) -> list[Round]: ...

    async def due_rounds(self, now: datetime) -> list[Round]: ...

    async def add_negotiation(self, negotiation: Negotiation) -> Negotiation: ...

    async def update_negotiation(self, negotiation_id: int, **fields: Any) -> None: ...

    async def negotiations_for(self, po_name: str, product_id: int | None) -> list[Negotiation]: ...


def _js(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _round(row: dict[str, Any], rfqs: list[RoundRfq]) -> Round:
    return Round(
        id=int(row["id"]),
        case_id=str(row["case_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        source_po_name=row.get("source_po_name"),
        incumbent_partner_id=row.get("incumbent_partner_id"),
        basket=[BasketLine.model_validate(b) for b in _js(row.get("basket")) or []],
        deadline=row["deadline"],
        group_id=row.get("group_id"),
        rfqs=rfqs,
        comparison=_js(row.get("comparison")),
        award_approval_id=row.get("award_approval_id"),
        awarded_partner_id=row.get("awarded_partner_id"),
        awarded_po_name=row.get("awarded_po_name"),
        created_by=row.get("created_by"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _rfq(row: dict[str, Any]) -> RoundRfq:
    return RoundRfq(
        partner_id=int(row["partner_id"]),
        partner_name=str(row["partner_name"]),
        po_id=row.get("po_id"),
        po_name=row.get("po_name"),
        status=str(row.get("status") or "created"),  # type: ignore[arg-type]
        thread_id=row.get("thread_id"),
        sent_at=row.get("sent_at"),
        replied_at=row.get("replied_at"),
    )


def _negotiation(row: dict[str, Any]) -> Negotiation:
    return Negotiation(
        id=int(row["id"]),
        case_id=str(row["case_id"]),
        po_id=int(row["po_id"]),
        po_name=str(row["po_name"]),
        partner_id=int(row["partner_id"]),
        product_id=int(row["product_id"]),
        round_no=int(row["round_no"]),
        current_price=float(row["current_price"]),
        offered_price=float(row["offered_price"]),
        floor_price=float(row["floor_price"]),
        target_price=float(row["target_price"]),
        status=str(row.get("status") or "proposed"),  # type: ignore[arg-type]
        approval_id=row.get("approval_id"),
        created_at=row["created_at"],
    )


ROUND_FIELDS = (
    "status",
    "group_id",
    "comparison",
    "award_approval_id",
    "awarded_partner_id",
    "awarded_po_name",
    "deadline",
)
RFQ_FIELDS = ("po_id", "po_name", "status", "thread_id", "sent_at", "replied_at")


class PostgresRoundStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_round(
        self,
        *,
        case_id: str,
        basket: list[BasketLine],
        deadline: datetime,
        source_po_name: str | None,
        incumbent_partner_id: int | None,
        created_by: str | None,
    ) -> Round:
        row = await self._db.fetch_one(
            "INSERT INTO sourcing_rounds (case_id, basket, deadline, source_po_name, "
            "incumbent_partner_id, created_by) VALUES (%s, %s::jsonb, %s, %s, %s, %s) RETURNING *",
            (
                case_id,
                json.dumps([b.model_dump(mode="json") for b in basket]),
                deadline,
                source_po_name,
                incumbent_partner_id,
                created_by,
            ),
        )
        assert row is not None
        return _round(row, [])

    async def get_round(self, round_id: int) -> Round | None:
        row = await self._db.fetch_one("SELECT * FROM sourcing_rounds WHERE id = %s", (round_id,))
        return await self._with_rfqs(row) if row else None

    async def round_for_case(self, case_id: str) -> Round | None:
        row = await self._db.fetch_one(
            "SELECT * FROM sourcing_rounds WHERE case_id = %s ORDER BY id DESC LIMIT 1", (case_id,)
        )
        return await self._with_rfqs(row) if row else None

    async def open_round_for_po(self, po_name: str) -> Round | None:
        row = await self._db.fetch_one(
            "SELECT * FROM sourcing_rounds WHERE source_po_name = %s AND status = ANY(%s) "
            "ORDER BY id DESC LIMIT 1",
            (po_name, list(ACTIVE)),
        )
        return await self._with_rfqs(row) if row else None

    async def update_round(self, round_id: int, **fields: Any) -> Round:
        sets, values = [], []
        for key in fields:
            if key not in ROUND_FIELDS:
                continue
            value = fields[key]
            if key == "comparison":
                sets.append("comparison = %s::jsonb")
                values.append(json.dumps(value, default=str) if value is not None else None)
            else:
                sets.append(f"{key} = %s")
                values.append(value)
        row = await self._db.fetch_one(
            f"UPDATE sourcing_rounds SET {', '.join([*sets, 'updated_at = now()'])} "
            "WHERE id = %s RETURNING *",
            (*values, round_id),
        )
        if row is None:
            raise NotFound(f"sourcing round {round_id} not found")
        return await self._with_rfqs(row)

    async def set_rfqs(self, round_id: int, rfqs: list[RoundRfq]) -> None:
        await self._db.execute("DELETE FROM sourcing_round_rfqs WHERE round_id = %s", (round_id,))
        for rfq in rfqs:
            await self._db.execute(
                "INSERT INTO sourcing_round_rfqs (round_id, partner_id, partner_name, po_id, "
                "po_name, status, thread_id, sent_at, replied_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    round_id,
                    rfq.partner_id,
                    rfq.partner_name,
                    rfq.po_id,
                    rfq.po_name,
                    rfq.status,
                    rfq.thread_id,
                    rfq.sent_at,
                    rfq.replied_at,
                ),
            )

    async def update_rfq(self, round_id: int, partner_id: int, **fields: Any) -> None:
        sets = [f"{k} = %s" for k in fields if k in RFQ_FIELDS]
        values = [fields[k] for k in fields if k in RFQ_FIELDS]
        if not sets:
            return
        await self._db.execute(
            f"UPDATE sourcing_round_rfqs SET {', '.join(sets)} "
            "WHERE round_id = %s AND partner_id = %s",
            (*values, round_id, partner_id),
        )

    async def rounds(
        self, *, status: str | None = None, partner_id: int | None = None, limit: int = 100
    ) -> list[Round]:
        clauses: list[str] = []
        params: list[Any] = []
        if status == "active":
            clauses.append("status = ANY(%s)")
            params.append(list(ACTIVE))
        elif status:
            clauses.append("status = %s")
            params.append(status)
        if partner_id is not None:
            clauses.append("id IN (SELECT round_id FROM sourcing_round_rfqs WHERE partner_id = %s)")
            params.append(partner_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._db.fetch_all(
            f"SELECT * FROM sourcing_rounds {where} ORDER BY id DESC LIMIT %s",
            (*params, limit),
        )
        return [await self._with_rfqs(r) for r in rows]

    async def due_rounds(self, now: datetime) -> list[Round]:
        rows = await self._db.fetch_all(
            "SELECT * FROM sourcing_rounds WHERE status = 'open' ORDER BY id"
        )
        out = []
        for row in rows:
            item = await self._with_rfqs(row)
            if is_due(item, now):
                out.append(item)
        return out

    async def add_negotiation(self, negotiation: Negotiation) -> Negotiation:
        row = await self._db.fetch_one(
            "INSERT INTO sourcing_negotiations (case_id, po_id, po_name, partner_id, product_id, "
            "round_no, current_price, offered_price, floor_price, target_price, status, "
            "approval_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                negotiation.case_id,
                negotiation.po_id,
                negotiation.po_name,
                negotiation.partner_id,
                negotiation.product_id,
                negotiation.round_no,
                negotiation.current_price,
                negotiation.offered_price,
                negotiation.floor_price,
                negotiation.target_price,
                negotiation.status,
                negotiation.approval_id,
            ),
        )
        assert row is not None
        return _negotiation(row)

    async def update_negotiation(self, negotiation_id: int, **fields: Any) -> None:
        allowed = ("status", "approval_id", "offered_price")
        sets = [f"{k} = %s" for k in fields if k in allowed]
        values = [fields[k] for k in fields if k in allowed]
        if sets:
            await self._db.execute(
                f"UPDATE sourcing_negotiations SET {', '.join(sets)} WHERE id = %s",
                (*values, negotiation_id),
            )

    async def negotiations_for(self, po_name: str, product_id: int | None) -> list[Negotiation]:
        if product_id is None:
            rows = await self._db.fetch_all(
                "SELECT * FROM sourcing_negotiations WHERE po_name = %s ORDER BY id", (po_name,)
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM sourcing_negotiations WHERE po_name = %s AND product_id = %s "
                "ORDER BY id",
                (po_name, product_id),
            )
        return [_negotiation(r) for r in rows]

    async def _with_rfqs(self, row: dict[str, Any]) -> Round:
        rfq_rows = await self._db.fetch_all(
            "SELECT * FROM sourcing_round_rfqs WHERE round_id = %s ORDER BY id", (row["id"],)
        )
        return _round(row, [_rfq(r) for r in rfq_rows])


def is_due(item: Round, now: datetime) -> bool:
    """A round is compared when its deadline passed or every sent invitation was answered."""
    if item.status != "open":
        return False
    if item.deadline <= now:
        return True
    sent = [r for r in item.rfqs if r.status == "sent"]
    return bool(sent) and all(r.replied_at is not None for r in sent)


class MemoryRoundStore:
    def __init__(self) -> None:
        self.rounds_by_id: dict[int, Round] = {}
        self.negotiations: dict[int, Negotiation] = {}

    async def create_round(
        self,
        *,
        case_id: str,
        basket: list[BasketLine],
        deadline: datetime,
        source_po_name: str | None,
        incumbent_partner_id: int | None,
        created_by: str | None,
    ) -> Round:
        round_id = max(self.rounds_by_id, default=0) + 1
        now = datetime.now(UTC)
        item = Round(
            id=round_id,
            case_id=case_id,
            basket=basket,
            deadline=deadline,
            source_po_name=source_po_name,
            incumbent_partner_id=incumbent_partner_id,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.rounds_by_id[round_id] = item
        return item

    async def get_round(self, round_id: int) -> Round | None:
        return self.rounds_by_id.get(round_id)

    async def round_for_case(self, case_id: str) -> Round | None:
        for item in sorted(self.rounds_by_id.values(), key=lambda r: -r.id):
            if item.case_id == case_id:
                return item
        return None

    async def open_round_for_po(self, po_name: str) -> Round | None:
        for item in sorted(self.rounds_by_id.values(), key=lambda r: -r.id):
            if item.source_po_name == po_name and item.status in ACTIVE:
                return item
        return None

    async def update_round(self, round_id: int, **fields: Any) -> Round:
        item = self.rounds_by_id.get(round_id)
        if item is None:
            raise NotFound(f"sourcing round {round_id} not found")
        allowed = {k: v for k, v in fields.items() if k in ROUND_FIELDS}
        updated = item.model_copy(update={**allowed, "updated_at": datetime.now(UTC)})
        self.rounds_by_id[round_id] = updated
        return updated

    async def set_rfqs(self, round_id: int, rfqs: list[RoundRfq]) -> None:
        item = self.rounds_by_id[round_id]
        self.rounds_by_id[round_id] = item.model_copy(update={"rfqs": list(rfqs)})

    async def update_rfq(self, round_id: int, partner_id: int, **fields: Any) -> None:
        item = self.rounds_by_id[round_id]
        rfqs = [
            r.model_copy(update={k: v for k, v in fields.items() if k in RFQ_FIELDS})
            if r.partner_id == partner_id
            else r
            for r in item.rfqs
        ]
        self.rounds_by_id[round_id] = item.model_copy(update={"rfqs": rfqs})

    async def rounds(
        self, *, status: str | None = None, partner_id: int | None = None, limit: int = 100
    ) -> list[Round]:
        out = []
        for item in sorted(self.rounds_by_id.values(), key=lambda r: -r.id):
            if status == "active" and item.status not in ACTIVE:
                continue
            if status and status != "active" and item.status != status:
                continue
            if partner_id is not None and all(r.partner_id != partner_id for r in item.rfqs):
                continue
            out.append(item)
        return out[:limit]

    async def due_rounds(self, now: datetime) -> list[Round]:
        return [r for r in sorted(self.rounds_by_id.values(), key=lambda r: r.id) if is_due(r, now)]

    async def add_negotiation(self, negotiation: Negotiation) -> Negotiation:
        new_id = max(self.negotiations, default=0) + 1
        saved = negotiation.model_copy(update={"id": new_id})
        self.negotiations[new_id] = saved
        return saved

    async def update_negotiation(self, negotiation_id: int, **fields: Any) -> None:
        item = self.negotiations[negotiation_id]
        self.negotiations[negotiation_id] = item.model_copy(
            update={
                k: v for k, v in fields.items() if k in ("status", "approval_id", "offered_price")
            }
        )

    async def negotiations_for(self, po_name: str, product_id: int | None) -> list[Negotiation]:
        return [
            n
            for n in sorted(self.negotiations.values(), key=lambda n: n.id)
            if n.po_name == po_name and (product_id is None or n.product_id == product_id)
        ]


__all__ = ["ACTIVE", "MemoryRoundStore", "PostgresRoundStore", "RoundStore", "is_due"]
