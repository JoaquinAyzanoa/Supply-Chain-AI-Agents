"""An invoice by email or a bill typed in Odoo: read, matched, approved, drafted, never posted."""

from __future__ import annotations

from datetime import date
from typing import Any

from invoice_match.domain.models import PoCandidate
from invoice_match.testing import FakeInvoicePorts, demo_bill
from sc_core.llm.testing import ScriptedChatClient
from sc_core.schema.a2a import InvoiceData, InvoiceLine, InvoiceMatchTask

READ = InvoiceData(
    supplier_name="Proveedor Hidraulica",
    invoice_number="F001-000123",
    invoice_date=date(2026, 10, 3),
    currency="PEN",
    po_reference="P00015",
    lines=[
        InvoiceLine(description="Bomba hidraulica 2HP", qty=2, unit_price=500.0, total=1000.0),
        InvoiceLine(description='Manguera 1/2"', qty=20, unit_price=12.5, total=250.0),
    ],
    subtotal=1250.0,
    tax=225.0,
    total=1475.0,
    confidence=0.95,
)


async def test_clean_invoice_by_email_becomes_a_draft_bill_after_approval(
    make_agent: Any, ports: FakeInvoicePorts, chat: ScriptedChatClient, approval_ports: Any
) -> None:
    chat.responses.append(READ)
    agent = make_agent()
    paused = await agent.run(
        InvoiceMatchTask(
            kind="match_bill", case_id="c_inv1", po_name="P00015", graph_message_id="inv1"
        )
    )
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101
    assert paused.invoice is not None and paused.invoice.invoice_number == "F001-000123"
    assert paused.match is not None and paused.match.verdict == "clean"
    [created] = approval_ports.created
    assert created["kind"] == "vendor_bill" and created["po_id"] == 7
    assert created["summary"] == (
        "Record invoice F001-000123 from Proveedor Hidraulica for P00015 (1475.00 PEN): it matches"
    )
    assert created["payload"]["verdict"] == "clean" and len(created["payload"]["lines"]) == 2
    assert (
        "FACTURA ELECTRONICA F001-000123" in chat.last_prompt_text()
    )  # the PDF text reached the model
    assert ports.created == [] and ports.checks == []  # nothing in Odoo before the decision
    assert created["res_model"] == "purchase.order" and created["res_id"] == 7  # no bill yet

    done = await agent.resume(
        "c_inv1", {"approval_id": 101, "status": "approved", "resolved_by": "Ana"}
    )
    assert (
        done.status == "applied" and done.bill_name == "BILL/2026/10/0001" and done.bill_id == 501
    )
    assert ports.created == [{"po_id": 7, "ref": "F001-000123", "invoice_date": date(2026, 10, 3)}]
    [(po_id, note)] = ports.base.notes
    assert po_id == 7 and "F001-000123" in note and "Ana" in note and "border" in note
    assert "Nothing is posted" in note
    # the new bill's "AI Agent" tab and chatter carry the verdict too
    assert ports.checks == [
        {
            "move_id": 501,
            "verdict": "clean",
            "po_id": 7,
            "summary": "All 2 line(s) match the order and the receipts.",
        }
    ]
    assert ports.bill_notes == [(501, note)]
    assert ports.runs[paused.run_id]["status"] == "applied"
    assert (await agent.snapshot("c_inv1")).get("attachments_text") is None


async def test_price_variance_is_held_and_approving_creates_the_bill_anyway(
    make_agent: Any, ports: FakeInvoicePorts, chat: ScriptedChatClient, approval_ports: Any
) -> None:
    dearer = READ.model_copy(
        update={
            "lines": [
                InvoiceLine(
                    description="Bomba hidraulica 2HP", qty=2, unit_price=515.0, total=1030.0
                ),
                InvoiceLine(description='Manguera 1/2"', qty=20, unit_price=12.5, total=250.0),
            ],
            "subtotal": 1280.0,
            "total": 1510.4,
        }
    )
    chat.responses.append(dearer)
    agent = make_agent()
    paused = await agent.run(
        InvoiceMatchTask(kind="match_bill", case_id="c_inv2", graph_message_id="inv1")
    )
    assert paused.status == "awaiting_approval"
    assert paused.match is not None and paused.match.verdict == "hold"
    assert paused.match.reasons == ["1 line(s) price variance"]
    [created] = approval_ports.created
    assert created["summary"].startswith("Invoice F001-000123 from Proveedor Hidraulica for P00015")
    assert created["summary"].endswith("does not match: decide")
    assert created["payload"]["lines"][0]["status"] == "price_variance"
    assert paused.po_name == "P00015"  # found from the invoice's reference, not the task

    rejected = await agent.resume(
        "c_inv2",
        {
            "approval_id": 101,
            "status": "rejected",
            "resolved_by": "Ana",
            "reason": "ask for a credit note",
        },
    )
    assert rejected.status == "rejected" and "ask for a credit note" in rejected.outcome.summary
    assert ports.created == [] and ports.checks == [] and ports.bill_notes == []  # no bill exists


async def test_clean_invoice_under_the_limit_needs_nobody(
    make_agent: Any, ports: FakeInvoicePorts, chat: ScriptedChatClient, approval_ports: Any
) -> None:
    from sc_core.schema.autonomy import AutonomyPolicy

    chat.responses.append(READ)
    cap = AutonomyPolicy.from_legacy(bill_auto_approve_amount=2000.0).provider()
    done = await make_agent(policy=cap).run(
        InvoiceMatchTask(
            kind="match_bill", case_id="c_inv3", po_name="P00015", graph_message_id="inv1"
        )
    )
    assert done.status == "applied" and approval_ports.created == []
    assert ports.created and ports.created[0]["ref"] == "F001-000123"


async def test_invoice_without_reference_is_matched_by_amount_or_escalated(
    make_agent: Any, ports: FakeInvoicePorts, chat: ScriptedChatClient
) -> None:
    ports.base.inbound["inv1"] = "Adjuntamos la factura."
    blind = READ.model_copy(update={"po_reference": None})
    ports.candidate_orders[42] = [
        PoCandidate(
            id=7,
            name="P00015",
            state="purchase",
            amount_total=1475.0,
            amount_untaxed=1250.0,
            currency="PEN",
        ),
        PoCandidate(
            id=8,
            name="P00016",
            state="purchase",
            amount_total=99.0,
            amount_untaxed=90.0,
            currency="PEN",
        ),
    ]
    chat.responses.append(blind)
    paused = await make_agent().run(
        InvoiceMatchTask(kind="match_bill", case_id="c_inv4", graph_message_id="inv1")
    )
    assert paused.status == "awaiting_approval" and paused.po_name == "P00015"

    ports.candidate_orders[42].append(
        PoCandidate(
            id=9,
            name="P00017",
            state="purchase",
            amount_total=1475.0,
            amount_untaxed=1250.0,
            currency="PEN",
        )
    )
    chat.responses.append(blind)
    lost = await make_agent().run(
        InvoiceMatchTask(kind="match_bill", case_id="c_inv5", graph_message_id="inv1")
    )
    assert (
        lost.status == "escalated"
        and "2 orders of this supplier have the same total" in lost.outcome.summary
    )


async def test_duplicate_invoice_number_is_left_alone(
    make_agent: Any, ports: FakeInvoicePorts, chat: ScriptedChatClient, approval_ports: Any
) -> None:
    ports.existing[(42, "F001-000123")] = (77, "BILL/2026/09/0009")
    chat.responses.append(READ)
    done = await make_agent().run(
        InvoiceMatchTask(
            kind="match_bill", case_id="c_inv6", po_name="P00015", graph_message_id="inv1"
        )
    )
    assert done.status == "no_action"
    assert done.outcome.summary == "invoice F001-000123 is already recorded as BILL/2026/09/0009"
    assert approval_ports.created == [] and ports.created == []


async def test_bill_typed_in_odoo_is_checked_without_the_model(
    make_agent: Any, ports: FakeInvoicePorts, chat: ScriptedChatClient, approval_ports: Any
) -> None:
    ports.bills[90] = demo_bill(price_unit=520.0)
    agent = make_agent()
    paused = await agent.run(InvoiceMatchTask(kind="match_bill", case_id="c_inv7", move_id=90))
    assert chat.calls == []  # a typed bill is already structured
    assert paused.status == "awaiting_approval" and paused.po_name == "P00015"
    assert paused.match is not None and paused.match.verdict == "hold"
    [created] = approval_ports.created
    assert created["payload"]["existing_bill_name"] == "BILL/2026/10/0003"
    # the approval hangs on the bill, and the invoice form shows the verdict before anyone decides
    assert created["res_model"] == "account.move" and created["res_id"] == 90
    assert ports.checks == [
        {"move_id": 90, "verdict": "hold", "po_id": 7, "summary": "Held: 1 line(s) price variance."}
    ]
    done = await agent.resume(
        "c_inv7", {"approval_id": 101, "status": "approved", "resolved_by": "Ana"}
    )
    assert done.status == "applied" and done.bill_id == 90 and ports.created == []  # no second bill
    assert any("BILL/2026/10/0003" in n and "does not match" in n for _, n in ports.base.notes)
    [(move_id, bill_note)] = ports.bill_notes
    assert move_id == 90 and "does not match" in bill_note and "Ana" in bill_note


async def test_unknown_bill_or_order_fails_plainly(make_agent: Any) -> None:
    missing = await make_agent().run(
        InvoiceMatchTask(kind="match_bill", case_id="c_inv8", move_id=404)
    )
    assert missing.status == "failed" and "bill 404 not found" in missing.outcome.summary


async def test_checking_an_existing_bill_ignores_its_own_quantities(
    make_agent: Any, ports: FakeInvoicePorts, approval_ports: Any
) -> None:
    """Odoo already counts a draft bill as invoiced; that must not read as "billed twice"."""
    from supplier_comms.domain.models import LineView

    from .conftest import received_context

    ctx = received_context()
    ports.base.contexts["P00015"] = ctx.model_copy(
        update={
            "lines": [
                LineView(**{**line.model_dump(), "qty_invoiced": line.qty}) for line in ctx.lines
            ]
        }
    )
    ports.bills[90] = demo_bill(price_unit=500.0)
    paused = await make_agent().run(
        InvoiceMatchTask(kind="match_bill", case_id="c_inv9", move_id=90)
    )
    assert paused.match is not None and paused.match.verdict == "clean"
    assert [m.status for m in paused.match.lines] == ["ok"]
    [created] = approval_ports.created
    assert created["summary"].endswith("it matches")


async def test_the_control_tower_tolerance_wins_over_the_environment(
    make_agent: Any, ports: FakeInvoicePorts, chat: ScriptedChatClient, approval_ports: Any
) -> None:
    """A 3 % dearer line is held at the default 1 %, matched once a person raised it to 5 %."""
    from sc_core.infra.runtime_settings import MemoryRuntimeSettingsReader
    from sc_core.schema.runtime_settings import RuntimeSettings

    dearer = READ.model_copy(
        update={
            "lines": [
                InvoiceLine(
                    description="Bomba hidraulica 2HP", qty=2, unit_price=515.0, total=1030.0
                ),
                InvoiceLine(description='Manguera 1/2"', qty=20, unit_price=12.5, total=250.0),
            ],
            "subtotal": 1280.0,
            "total": 1510.4,
        }
    )
    chat.responses.append(dearer)
    runtime = MemoryRuntimeSettingsReader(RuntimeSettings(invoice_price_tolerance_pct=5.0))
    paused = await make_agent(runtime=runtime).run(
        InvoiceMatchTask(kind="match_bill", case_id="c_tol", graph_message_id="inv1")
    )
    assert paused.match is not None and paused.match.verdict == "clean"
    assert approval_ports.created[-1]["summary"].endswith("it matches")
