"""Approval of a run and the writes an approved run unlocks.

One ``sc.approval`` of kind ``planning_run`` per run, hung on the
warehouse; its payload is the totals, the summary and the exception list.
The decision may carry ``details.accepted_line_ids`` and ``details.edits``
(from the Control Tower); a plain approval from Odoo accepts every line
whose action is not ``manual_review``, ``hold`` or ``none``.

Apply is idempotent: reorder rules are written by id or created once per
product, RFQs use ``external_ref = plan:<run>:<supplier>:<warehouse>`` so a
second resume finds them instead of creating twice, and every RFQ that was
created is announced with ``rfq.drafted`` for the supplier agent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from math import ceil
from typing import Any

from loguru import logger

from inventory_planning import AGENT_NAME
from inventory_planning.nodes.propose import dataset_of, lines_of, task_of
from inventory_planning.policy import HoldStore, ParamsStore, ProductParams
from inventory_planning.ports import WritePorts
from inventory_planning.runs import RunStore
from inventory_planning.state import Node
from sc_core.graph import ApprovalRequest
from sc_core.graph.approval import decision_for
from sc_core.i18n import Language, t
from sc_core.schema.a2a import AppliedSummary, Need
from sc_core.schema.events import BaseEvent, NeedsProposed, event_id_for
from sc_core.schema.planning import ReplenishmentLine, ReplenishmentProposal

PLAN_STEP = "planning_run"
ACTIONABLE = ("update_rule", "create_rfq", "update_rule_and_rfq", "consolidate")


def make_plan_approval(
    *, language: Language = "en"
) -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
    async def build(state: dict[str, Any]) -> ApprovalRequest:
        proposal = ReplenishmentProposal.model_validate(state["proposal"])
        totals = proposal.totals
        exceptions = [
            {
                "line_id": ln.line_id,
                "product_ref": ln.product_ref,
                "exception": ln.exception,
                "action": ln.action,
                "order_qty": ln.order_qty,
                "explanation": ln.explanation,
            }
            for ln in proposal.lines
            if ln.exception
        ]
        summary = t(
            "plan.approval_summary",
            language,
            date=proposal.as_of.isoformat(),
            rfqs=int(totals.get("rfq_lines", 0)),
            rules=int(totals.get("rules_changed", 0)),
            exceptions=int(totals.get("exceptions", 0)),
        )
        return ApprovalRequest(
            kind="planning_run",
            summary=summary[:200],
            payload={
                "run_id": proposal.run_id,
                "as_of": proposal.as_of.isoformat(),
                "warehouse_code": proposal.warehouse_code,
                "summary": proposal.summary,
                "totals": totals,
                "exceptions": exceptions,
                "lines": [
                    {
                        "line_id": ln.line_id,
                        "product_ref": ln.product_ref,
                        "action": ln.action,
                        "order_qty": ln.order_qty,
                        "proposed_min": ln.proposed_min,
                        "proposed_max": ln.proposed_max,
                        "supplier": ln.supplier_name,
                    }
                    for ln in proposal.lines
                    if ln.action in ACTIONABLE
                ],
            },
            res_model="stock.warehouse",
            res_id=proposal.warehouse_id,
            review_on_approval=True,  # stock.warehouse has no activities; the approval does
        )

    return build


def accepted_lines(
    lines: list[ReplenishmentLine], decision: dict[str, Any] | None
) -> list[ReplenishmentLine]:
    """Lines to apply: what the decision names, else every actionable line; edits applied."""
    details = (decision or {}).get("details") or {}
    wanted = details.get("accepted_line_ids")
    edits: dict[str, dict[str, Any]] = details.get("edits") or {}
    chosen = [
        ln
        for ln in lines
        if (ln.line_id in wanted if wanted is not None else ln.action in ACTIONABLE)
    ]
    out = []
    for line in chosen:
        edit = edits.get(line.line_id)
        if edit:
            allowed = {
                k: float(ceil(float(v) - 1e-9))  # a person's quantities are whole units too
                for k, v in edit.items()
                if k in ("order_qty", "proposed_min", "proposed_max")
            }
            line = line.model_copy(update=allowed)
        out.append(line)
    return out


async def remember_decision(
    proposed: list[ReplenishmentLine],
    decision: Any,
    *,
    params_store: ParamsStore | None,
    holds: HoldStore | None,
    today: date,
) -> None:
    """What the person taught: rule changes left out are counted (two make a hold);
    parameters kept from a what-if become the product's own."""
    details = (decision.details if decision else None) or {}
    accepted = details.get("accepted_line_ids")
    if holds is not None and accepted is not None:
        wanted = {str(i) for i in accepted}
        for line in proposed:
            if line.action in ("update_rule", "update_rule_and_rfq") and line.line_id not in wanted:
                await holds.note_rejection(
                    line.product_id,
                    approval_id=decision.approval_id if decision else None,
                    today=today,
                )
    kept: dict[str, dict[str, Any]] = details.get("params") or {}
    if params_store is not None and kept:
        by_line = {line.line_id: line for line in proposed}
        for line_id, values in kept.items():
            target = by_line.get(line_id)
            if target is None:
                continue
            allowed = {
                k: v
                for k, v in values.items()
                if k in ("service_level", "review_period_days", "max_coverage_days")
                and v is not None
            }
            if not allowed:
                continue
            current = (await params_store.for_products([target.product_id])).get(target.product_id)
            base = current or ProductParams.default_for(target.product_id, target.abc_class)  # type: ignore[arg-type]
            await params_store.save(base.model_copy(update={**allowed, "source": "planner"}))


def make_apply(
    ports: WritePorts,
    runs: RunStore,
    *,
    publish: Callable[[BaseEvent], Awaitable[Any]],
    today: Callable[[], date],
    language: Language = "en",
    params_store: ParamsStore | None = None,
    holds: HoldStore | None = None,
) -> Node:
    async def apply(state: Any) -> dict[str, Any]:
        if task_of(state).kind == "what_if":  # never reached by the graph; belt and braces
            return {
                "outcome": {"status": "failed", "summary": t("plan.what_if_never_writes", language)}
            }
        dataset = dataset_of(state)
        decision = decision_for(state, PLAN_STEP)
        lines = accepted_lines(lines_of(state), decision.model_dump() if decision else None)
        await remember_decision(
            lines_of(state), decision, params_store=params_store, holds=holds, today=today()
        )
        applied: dict[str, dict[str, Any]] = {}
        rules_written = 0
        for line in lines:
            if line.action in ("update_rule", "update_rule_and_rfq") and line.proposed_max > 0:
                product = dataset.product(line.product_id)
                existing = product.orderpoint.id if product and product.orderpoint else None
                orderpoint_id = await ports.set_orderpoint(
                    product_id=line.product_id,
                    warehouse_id=line.warehouse_id,
                    minimum=line.proposed_min,
                    maximum=line.proposed_max,
                    existing_id=existing,
                )
                applied.setdefault(line.line_id, {})["orderpoint_id"] = orderpoint_id
                applied[line.line_id]["min_max"] = [line.proposed_min, line.proposed_max]
                rules_written += 1
        # The buys are needs for the sourcing agent: it asks the suppliers who list each
        # product, and a person awards. The planner names no supplier; its price and
        # supplier fields are the reference (what we last paid, who listed it).
        needs: list[Need] = []
        for line in lines:
            if (
                line.action in ("create_rfq", "update_rule_and_rfq", "consolidate")
                and line.order_qty > 0
            ):
                needs.append(
                    Need(
                        product_id=line.product_id,
                        product=line.product_ref,
                        qty=line.order_qty,
                        expected_price=line.unit_price,
                        currency=line.currency,
                        need_date=today() + timedelta(days=int(line.lead_time_days or 0)),
                        source_line_id=line.line_id,
                    )
                )
                applied.setdefault(line.line_id, {})["order_qty"] = line.order_qty
                applied[line.line_id]["need"] = True
        if needs:
            await publish(
                NeedsProposed(
                    event_id=event_id_for("sourcing.needs", state["run_id"]),
                    source=AGENT_NAME,
                    case_id=f"plan_needs_{state['run_id']}",
                    run_id=state["run_id"],
                    warehouse_id=dataset.warehouse_id,
                    needs=needs,
                )
            )
        created: list[str] = []
        existing_rfqs: list[str] = []
        await runs.mark_applied(state["run_id"], applied, accepted={ln.line_id for ln in lines})
        await runs.set_status(
            state["run_id"],
            "applied",
            approval_id=decision.approval_id if decision and decision.approval_id else None,
        )
        summary = AppliedSummary(
            orderpoints_written=rules_written,
            needs_sent=len(needs),
            rfqs_created=created,
            rfqs_existing=existing_rfqs,
            lines_applied=len(lines),
        )
        logger.bind(run_id=state["run_id"], **summary.model_dump()).info("plan applied")
        text = (
            t("plan.applied", language, rules=rules_written, needs=len(needs))
            + (f" ({', '.join(created)})" if created else "")
            + (t("plan.existing", language, n=len(existing_rfqs)) if existing_rfqs else "")
        )
        return {
            "applied": summary.model_dump(mode="json"),
            "outcome": {"status": "applied", "summary": text[:500]},
        }

    return apply


def make_rejected(runs: RunStore, *, language: Language = "en") -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        decision = decision_for(state, PLAN_STEP)
        await runs.set_status(
            state["run_id"],
            "rejected",
            approval_id=decision.approval_id if decision and decision.approval_id else None,
        )
        reason = (decision.reason if decision else None) or t("plan.rejected_default", language)
        summary = t("plan.rejected", language, reason=reason)
        return {"outcome": {"status": "rejected", "summary": summary[:500]}}

    return rejected


def _at_noon(day: date) -> Any:
    from datetime import UTC, datetime

    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
