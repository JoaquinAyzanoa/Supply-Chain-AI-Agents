"""A2A contracts: validation rules and JSON-schema snapshots.

The snapshots in ``tests/fixtures/schemas`` are the frozen v1 contracts. A
schema change fails here until the fixture is regenerated on purpose with
``just schema-snapshot`` (which sets ``SC_SCHEMA_UPDATE=1``).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from sc_core.schema.a2a import (
    CONTRACTS,
    ChangeProposal,
    OutboundDraft,
    Outcome,
    ProposedChange,
    QuotationData,
    QuotedLine,
    SupplierCommsResult,
    SupplierCommsTask,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "schemas"
UPDATE = os.environ.get("SC_SCHEMA_UPDATE") == "1"


@pytest.mark.parametrize("name", sorted(CONTRACTS))
def test_json_schema_matches_snapshot(name: str) -> None:
    model = CONTRACTS[name]
    version = getattr(model, "SCHEMA_VERSION", 1)
    path = FIXTURES / f"{name}.v{version}.json"
    current = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(current, encoding="utf-8")
    stored = path.read_text(encoding="utf-8")
    assert current == stored, (
        f"{name} schema changed; bump SCHEMA_VERSION or run `just schema-snapshot` on purpose"
    )


def test_task_kinds_require_their_fields() -> None:
    SupplierCommsTask(kind="send_rfq", case_id="c", po_name="P00015")
    SupplierCommsTask(kind="handle_inbound", case_id="c", po_name="P00015", graph_message_id="m")
    SupplierCommsTask(kind="resolve_unlinked", case_id="c", graph_message_id="m")
    with pytest.raises(ValidationError, match="needs po_name"):
        SupplierCommsTask(kind="request_eta", case_id="c")
    with pytest.raises(ValidationError, match="needs graph_message_id"):
        SupplierCommsTask(kind="handle_inbound", case_id="c", po_name="P00015")
    with pytest.raises(ValidationError):
        SupplierCommsTask(kind="send_rfq", case_id="c", po_name="P1", subject="no")  # type: ignore[call-arg]


def test_task_and_result_roundtrip_json() -> None:
    task = SupplierCommsTask(kind="follow_up", case_id="case_1", po_name="P00015", days_silent=5)
    assert SupplierCommsTask.model_validate_json(task.model_dump_json()) == task
    result = SupplierCommsResult(
        kind="handle_inbound",
        case_id="case_1",
        run_id="run_1",
        outcome=Outcome(status="awaiting_approval", summary="2 changes proposed", approval_id=9),
        po_name="P00015",
        extracted=QuotationData(
            lines=[QuotedLine(description="Bomba", unit_price=120.5, currency="USD")],
            eta_date_raw="20 de octubre",
            eta_date=date(2026, 10, 20),
        ),
        proposal=ChangeProposal(
            changes=[
                ProposedChange(
                    po_line_id=3,
                    product="Bomba",
                    field="date_planned",
                    before="2026-10-01",
                    after="2026-10-20",
                    confidence=0.9,
                ),
                ProposedChange(
                    po_line_id=3,
                    product="Bomba",
                    field="price",
                    before="100.00 PEN",
                    after="120.50 USD",
                    confidence=0.8,
                    needs_review=True,
                    review_reason="currency differs from the order",
                ),
            ],
            summary="ETA moved; price in another currency",
        ),
    )
    parsed = SupplierCommsResult.model_validate_json(result.model_dump_json())
    assert parsed == result and parsed.status == "awaiting_approval"
    assert parsed.proposal is not None and parsed.proposal.needs_review
    assert [c.field for c in parsed.proposal.applicable] == ["date_planned"]


def test_outbound_draft_requires_a_body_and_recipients() -> None:
    with pytest.raises(ValidationError):
        OutboundDraft(kind="rfq", to=[], subject="s", html_body="<p>x</p>")
    with pytest.raises(ValidationError):
        OutboundDraft(kind="rfq", to=["a@b"], subject="s", html_body="")
