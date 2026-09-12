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
from typing import Any

from loguru import logger

from inventory_planning import AGENT_NAME
from inventory_planning.nodes.propose import dataset_of, lines_of, task_of
from inventory_planning.ports import WritePorts
from inventory_planning.runs import RunStore
from inventory_planning.state import Node
from sc_core.graph import ApprovalRequest
from sc_core.graph.approval import decision_for
from sc_core.odoo.models import NewOrderLine
from sc_core.schema.a2a import AppliedSummary
from sc_core.schema.events import RfqDrafted, event_id_for
from sc_core.schema.planning import ReplenishmentLine, ReplenishmentProposal

PLAN_STEP = "planning_run"
ACTIONABLE = ("update_rule", "create_rfq", "update_rule_and_rfq")


def make_plan_approval() -> Callable[[dict[str, Any]], Awaitable[ApprovalRequest]]:
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
        summary = (
            f"Plan de reposición {proposal.as_of.isoformat()}: "
            f"{int(totals.get('rfq_lines', 0))} cotizaciones, "
            f"{int(totals.get('rules_changed', 0))} reglas, "
            f"{int(totals.get('exceptions', 0))} excepciones"
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
                k: float(v)
                for k, v in edit.items()
                if k in ("order_qty", "proposed_min", "proposed_max")
            }
            line = line.model_copy(update=allowed)
        out.append(line)
    return out


def make_apply(
    ports: WritePorts,
    runs: RunStore,
    *,
    publish: Callable[[RfqDrafted], Awaitable[Any]],
    today: Callable[[], date],
) -> Node:
    async def apply(state: Any) -> dict[str, Any]:
        if task_of(state).kind == "what_if":  # never reached by the graph; belt and braces
            return {"outcome": {"status": "failed", "summary": "what_if runs never write"}}
        dataset = dataset_of(state)
        decision = decision_for(state, PLAN_STEP)
        lines = accepted_lines(lines_of(state), decision.model_dump() if decision else None)
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
        created: list[str] = []
        existing_rfqs: list[str] = []
        by_supplier: dict[int, list[ReplenishmentLine]] = {}
        for line in lines:
            if line.action in ("create_rfq", "update_rule_and_rfq") and line.order_qty > 0:
                if line.supplier_id is None:
                    continue
                by_supplier.setdefault(line.supplier_id, []).append(line)
        for supplier_id, group in sorted(by_supplier.items()):
            external_ref = f"plan:{state['run_id']}:{supplier_id}:{dataset.warehouse_id}"
            planned = today() + timedelta(days=int(group[0].lead_time_days or 0))
            po_id, po_name, was_created = await ports.create_rfq(
                partner_id=supplier_id,
                lines=[
                    NewOrderLine(
                        product_id=ln.product_id,
                        product_qty=ln.order_qty,
                        price_unit=ln.unit_price,
                        date_planned=_at_noon(planned),
                    )
                    for ln in group
                ],
                external_ref=external_ref,
                origin=f"plan {state['run_id']}",
            )
            for ln in group:
                applied.setdefault(ln.line_id, {})["po_name"] = po_name
                applied[ln.line_id]["po_id"] = po_id
                applied[ln.line_id]["order_qty"] = ln.order_qty
            if was_created:
                created.append(po_name)
                await publish(
                    RfqDrafted(
                        event_id=event_id_for("rfq.drafted", po_id),
                        source=AGENT_NAME,
                        case_id=f"plan_rfq_{po_id}",
                        po_id=po_id,
                        po_name=po_name,
                        partner_id=supplier_id,
                        run_id=state["run_id"],
                        line_count=len(group),
                    )
                )
            else:
                existing_rfqs.append(po_name)
        await runs.mark_applied(state["run_id"], applied, accepted={ln.line_id for ln in lines})
        await runs.set_status(
            state["run_id"],
            "applied",
            approval_id=decision.approval_id if decision and decision.approval_id else None,
        )
        summary = AppliedSummary(
            orderpoints_written=rules_written,
            rfqs_created=created,
            rfqs_existing=existing_rfqs,
            lines_applied=len(lines),
        )
        logger.bind(run_id=state["run_id"], **summary.model_dump()).info("plan applied")
        text = (
            f"{rules_written} reglas escritas, {len(created)} solicitudes de cotización creadas"
            + (f" ({', '.join(created)})" if created else "")
            + (f", {len(existing_rfqs)} ya existían" if existing_rfqs else "")
        )
        return {
            "applied": summary.model_dump(mode="json"),
            "outcome": {"status": "applied", "summary": text[:500]},
        }

    return apply


def make_rejected(runs: RunStore) -> Node:
    async def rejected(state: Any) -> dict[str, Any]:
        decision = decision_for(state, PLAN_STEP)
        await runs.set_status(
            state["run_id"],
            "rejected",
            approval_id=decision.approval_id if decision and decision.approval_id else None,
        )
        reason = (decision.reason if decision else None) or "rechazado por el aprobador"
        return {"outcome": {"status": "rejected", "summary": f"plan rechazado: {reason}"[:500]}}

    return rejected


def _at_noon(day: date) -> Any:
    from datetime import UTC, datetime

    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
