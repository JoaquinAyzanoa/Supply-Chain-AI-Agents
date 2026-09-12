"""The approver's To-Do note reads like a message to a buyer, per approval kind."""

from __future__ import annotations

from sc_core.graph.approval import ApprovalRequest
from sc_core.graph.notes import render_note

CT = "http://localhost:8010"


def test_email_note_shows_recipients_subject_preview_and_links() -> None:
    req = ApprovalRequest(
        kind="send_email",
        summary="Send reminder to Proveedor for P00015",
        payload={
            "to": ["ventas@proveedor.com"],
            "subject": "[P00015] Reminder",
            "html_body": "<p>Dear supplier,</p><p>Please confirm <b>the date</b>.</p>",
            "draft_id": "AQMkADAwATM3Zm",
            "web_link": "https://outlook.live.com/owa/?ItemID=abc&x=1",
            "attachments": ["P00015.pdf"],
            "step": "send_email",
        },
    )
    html = render_note(req, language="en", control_tower_url=CT)
    assert "<b>To:</b> ventas@proveedor.com" in html
    assert "<b>Subject:</b> [P00015] Reminder" in html
    assert "<blockquote>Dear supplier, Please confirm the date.</blockquote>" in html
    assert "P00015.pdf" in html
    assert "AQMkADAwATM3Zm" not in html and "draft_id" not in html and "step" not in html
    assert 'href="https://outlook.live.com/owa/?ItemID=abc&amp;x=1">Open in Outlook</a>' in html
    assert f'href="{CT}">Review in the Control Tower</a>' in html
    spanish = render_note(req, language="es")
    assert "<b>Para:</b>" in spanish and "Control" not in spanish  # no URL, no link


def test_change_note_is_a_table_with_review_marks() -> None:
    req = ApprovalRequest(
        kind="po_change",
        summary="Proposed changes on P00021",
        payload={
            "po_name": "P00021",
            "changes": [
                {
                    "po_line_id": 78,
                    "product": "Flow control valve",
                    "field": "date_planned",
                    "before": "2026-10-31",
                    "after": "2026-10-20",
                    "source": "supplier",
                    "confidence": 0.95,
                    "needs_review": False,
                },
                {
                    "po_line_id": 79,
                    "product": "Hose",
                    "field": "price",
                    "before": 10,
                    "after": 12,
                    "source": "supplier",
                    "confidence": 0.4,
                    "needs_review": True,
                    "review_reason": "currency mismatch",
                },
            ],
            "classification": {"kind": "eta_update"},
        },
    )
    html = render_note(req, language="en")
    assert "<th>Product</th><th>Change</th><th>Before</th><th>After</th>" in html
    assert "<td>Flow control valve</td><td>delivery date</td><td>2026-10-31</td>" in html
    assert "<b>2026-10-20</b>" in html and "supplier · 95%" in html
    assert "needs review: currency mismatch" in html
    assert "classification" not in html and "po_line_id" not in html


def test_plan_and_escalation_notes() -> None:
    plan = ApprovalRequest(
        kind="planning_run",
        summary="Replenishment plan 2026-09-12",
        payload={
            "run_id": "run_1",
            "summary": "14 rules reviewed.",
            "totals": {"lines": 14, "rfq_lines": 3, "rules_changed": 11, "exceptions": 2},
            "exceptions": [
                {"product_ref": "AAA-T11A", "exception": "overstock", "explanation": "195 days"}
            ],
            "lines": [{"line_id": "run_1:1"}],
        },
        res_model="stock.warehouse",
        res_id=1,
    )
    html = render_note(plan, language="en", control_tower_url=CT)
    assert "14 lines to act on: 3 RFQ lines, 11 rule changes, 2 exceptions." in html
    assert "<b>AAA-T11A</b> · overstock — 195 days" in html
    assert f'href="{CT}/planning/run_1"' in html and "line_id" not in html

    escalation = ApprovalRequest(
        kind="escalation",
        summary="Planning case needs a person",
        payload={
            "reason": "an Odoo call failed",
            "details": {"agent": "inventory_planning"},
            "history": ["task_sent: daily_plan", "result: failed"],
            "trace_url": "http://langfuse/trace/x",
        },
        res_model="sc.approval",
        res_id=5,
    )
    html = render_note(escalation, language="es")
    assert "<p>an Odoo call failed</p>" in html
    assert "<b>Lo ocurrido hasta ahora:</b>" in html and "<li>result: failed</li>" in html
    assert "trace_url" not in html and "inventory_planning" not in html
