"""Repository tests on a scripted transport: what they ask Odoo and how they map answers."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from sc_core.odoo.client import OdooClient
from sc_core.odoo.models import NewOrderLine
from sc_core.odoo.repositories import (
    ActivityRepo,
    AgentRunRepo,
    ApprovalRepo,
    MailLinkRepo,
    OrderpointRepo,
    PartnerRepo,
    PickingRepo,
    PurchaseOrderRepo,
    SupplierInfoRepo,
)
from sc_core.shared.errors import NotFound, ValidationFailed

from . import samples
from .conftest import LOGIN_OK, ScriptedOdoo, rpc_ok

Factory = Callable[..., OdooClient]


# --- purchase orders ------------------------------------------------------------


async def test_get_by_name_requests_model_fields(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    odoo.script += [LOGIN_OK, rpc_ok([samples.PO_ROW])]
    po = await PurchaseOrderRepo(client_factory()).get_by_name("P00015")
    assert po is not None and po.name == "P00015"
    model, method, args, kwargs = odoo.execute_kw_args()
    assert (model, method, args[0]) == ("purchase.order", "search_read", [["name", "=", "P00015"]])
    assert "sc_external_ref" in kwargs["fields"] and kwargs["limit"] == 1


async def test_get_missing_raises_not_found(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok([])]  # Odoo 18 read() drops missing ids
    with pytest.raises(NotFound):
        await PurchaseOrderRepo(client_factory()).get(999)


async def test_create_rfq_is_idempotent(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok([samples.PO_ROW])]  # external ref already exists
    repo = PurchaseOrderRepo(client_factory())
    po = await repo.create_rfq(12, [NewOrderLine(product_id=32, product_qty=1)], external_ref="k1")
    assert po.id == 15
    assert [c for c in odoo.calls() if c[1] == "execute_kw"] == [("object", "execute_kw")]


async def test_create_rfq_payload(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    created = {
        **samples.PO_ROW,
        "id": 99,
        "name": "P00099",
        "state": "draft",
        "sc_external_ref": "k2",
    }
    odoo.script += [LOGIN_OK, rpc_ok([]), rpc_ok(99), rpc_ok([created])]
    repo = PurchaseOrderRepo(client_factory())
    po = await repo.create_rfq(
        12,
        [NewOrderLine(product_id=32, product_qty=2, price_unit=5.0)],
        external_ref="k2",
        origin="plan_1",
    )
    assert po.id == 99 and po.is_rfq
    _, method, args, _ = odoo.execute_kw_args(1)
    assert method == "create"
    assert args[0] == {
        "partner_id": 12,
        "sc_external_ref": "k2",
        "order_line": [[0, 0, {"product_id": 32, "product_qty": 2.0, "price_unit": 5.0}]],
        "origin": "plan_1",
    }


async def test_create_rfq_requires_lines(client_factory: Factory) -> None:
    with pytest.raises(ValidationFailed):
        await PurchaseOrderRepo(client_factory()).create_rfq(1, [], external_ref="x")


async def test_set_line_date_planned_writes_then_logs(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    odoo.script += [LOGIN_OK, rpc_ok(True), rpc_ok(True)]
    await PurchaseOrderRepo(client_factory()).set_line_date_planned(
        26, datetime(2026, 10, 20, 12, tzinfo=UTC), source="supplier", run_id="r1"
    )
    _, method, args, _ = odoo.execute_kw_args(0)
    assert method == "write" and args == [[26], {"date_planned": "2026-10-20 12:00:00"}]
    model, method, args, kwargs = odoo.execute_kw_args(1)
    assert (model, method, args) == ("purchase.order.line", "sc_log_eta_change", [[26]])
    assert kwargs["source"] == "supplier" and kwargs["run_id"] == "r1"


async def test_late_open_orders_domain(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok([samples.PO_ROW])]
    rows = await PurchaseOrderRepo(client_factory()).late_open_orders(date(2026, 9, 10))
    assert rows[0].name == "P00015"
    _, _, args, kwargs = odoo.execute_kw_args()
    assert args[0] == [
        ["state", "in", ["purchase"]],
        ["date_planned", "<", "2026-09-10"],
        ["receipt_status", "!=", "full"],
    ]
    assert kwargs["order"] == "date_planned asc"


async def test_set_eta_meta_validates_confidence(client_factory: Factory) -> None:
    with pytest.raises(ValidationFailed):
        await PurchaseOrderRepo(client_factory()).set_eta_meta(1, source="supplier", confidence=1.5)


# --- supplier info, orderpoints, pickings, partners -------------------------------


async def test_supplierinfo_for_product_searches_template_and_variant(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    odoo.script += [
        LOGIN_OK,
        rpc_ok([{"id": 32, "product_tmpl_id": [5, "Chair"]}]),
        rpc_ok([samples.SUPPLIERINFO_ROW]),
    ]
    rows = await SupplierInfoRepo(client_factory()).for_product(32)
    assert rows[0].partner_id.name == "Gemini Furniture"
    _, _, args, _ = odoo.execute_kw_args(1)
    assert args[0] == [
        ["product_tmpl_id", "=", 5],
        "|",
        ["product_id", "=", False],
        ["product_id", "=", 32],
    ]


async def test_upsert_price_updates_existing(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    updated = {**samples.SUPPLIERINFO_ROW, "price": 95.0}
    odoo.script += [LOGIN_OK, rpc_ok([samples.SUPPLIERINFO_ROW]), rpc_ok(True), rpc_ok([updated])]
    si = await SupplierInfoRepo(client_factory()).upsert_price(
        partner_id=11, product_tmpl_id=5, price=95.0, currency_id=1, min_qty=12.0, delay=3
    )
    assert si.price == 95.0
    _, method, args, _ = odoo.execute_kw_args(1)
    assert method == "write" and args == [[18], {"price": 95.0, "currency_id": 1, "delay": 3}]


async def test_orderpoint_validation_and_write(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    repo = OrderpointRepo(client_factory())
    with pytest.raises(ValidationFailed):
        await repo.set_min_max(1, minimum=10, maximum=5)
    odoo.script += [
        LOGIN_OK,
        rpc_ok(True),
        rpc_ok([{**samples.ORDERPOINT_ROW, "product_min_qty": 8.0}]),
    ]
    op = await repo.set_min_max(1, minimum=8, maximum=10)
    assert op.product_min_qty == 8.0
    _, method, args, _ = odoo.execute_kw_args(0)
    assert method == "write" and args == [[1], {"product_min_qty": 8, "product_max_qty": 10}]


async def test_picking_set_scheduled_date(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok(True), rpc_ok([samples.PICKING_ROW])]
    await PickingRepo(client_factory()).set_scheduled_date(
        13, datetime(2026, 10, 1, tzinfo=UTC), source="tracking", run_id="r9"
    )
    _, method, args, _ = odoo.execute_kw_args(0)
    assert args == [
        [13],
        {
            "scheduled_date": "2026-10-01 00:00:00",
            "sc_eta_source": "tracking",
            "sc_last_run_id": "r9",
        },
    ]


async def test_partner_lookups_normalise_email(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [LOGIN_OK, rpc_ok([samples.PARTNER_ROW]), rpc_ok([samples.PARTNER_ROW])]
    repo = PartnerRepo(client_factory())
    assert (await repo.find_by_email("  Azure.Interior24@Example.com ")) is not None
    assert (await repo.find_by_email_domain("Example.com"))[0].id == 15
    assert odoo.execute_kw_args(0)[2][0] == [
        ["email_normalized", "=", "azure.interior24@example.com"]
    ]
    assert odoo.execute_kw_args(1)[2][0] == [["email_normalized", "=ilike", "%@example.com"]]


# --- addon models -------------------------------------------------------------


async def test_activity_create_uses_activity_schedule(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    activity_row = {
        "id": 7,
        "res_model": "purchase.order",
        "res_id": 15,
        "summary": "Approve",
        "state": "planned",
    }
    odoo.script += [LOGIN_OK, rpc_ok([7]), rpc_ok([activity_row])]
    act = await ActivityRepo(client_factory()).create_approval(
        res_model="purchase.order",
        res_id=15,
        user_id=2,
        summary="Approve",
        note_html="<p>x</p>",
        deadline=date(2026, 9, 10),
    )
    assert act.id == 7
    model, method, args, kwargs = odoo.execute_kw_args(0)
    assert (model, method, args) == ("purchase.order", "activity_schedule", [[15]])
    assert kwargs["act_type_xmlid"] == "mail.mail_activity_data_todo"
    assert kwargs["date_deadline"] == "2026-09-10" and kwargs["user_id"] == 2


async def test_agent_run_start_is_idempotent_and_finish_uses_model_method(
    odoo: ScriptedOdoo, client_factory: Factory
) -> None:
    run_row = {
        "id": 1,
        "run_id": "r1",
        "agent": "supplier_comms",
        "status": "running",
        "case_id": "c1",
    }
    odoo.script += [LOGIN_OK, rpc_ok([run_row]), rpc_ok(1)]
    repo = AgentRunRepo(client_factory())
    run = await repo.start(run_id="r1", agent="supplier_comms", case_id="c1")
    assert run.id == 1
    assert await repo.finish("r1", "applied", "done") is True
    _, method, args, _ = odoo.execute_kw_args(1)
    assert method == "sc_finish" and args == ["r1", "applied", "done"]


async def test_approval_create_payload(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    odoo.script += [
        LOGIN_OK,
        rpc_ok(5),
        rpc_ok([{**samples.APPROVAL_ROW, "id": 5, "status": "pending"}]),
    ]
    repo = ApprovalRepo(client_factory())
    approval = await repo.create(
        kind="send_email",
        summary="Send RFQ",
        payload={"to": "a@b.com", "subject": "[P00015] RFQ"},
        requested_by="supplier_comms",
        case_id="c1",
        po_id=15,
        callback_url="http://director:8000/approvals/callback",
        callback_secret="s",
    )
    assert approval.id == 5 and approval.is_pending
    _, method, args, _ = odoo.execute_kw_args(0)
    assert method == "create"
    values = args[0]
    assert values["po_id"] == 15 and values["thread_id"] == "c1"
    assert json.loads(values["payload_json"]) == {"to": "a@b.com", "subject": "[P00015] RFQ"}
    assert values["callback_secret"] == "s"


async def test_approval_requires_target(client_factory: Factory) -> None:
    with pytest.raises(ValidationFailed):
        await ApprovalRepo(client_factory()).create(
            kind="escalation", summary="x", payload={}, requested_by="a", case_id="c"
        )


async def test_mail_link_is_idempotent(odoo: ScriptedOdoo, client_factory: Factory) -> None:
    link_row = {"id": 1, "po_id": [15, "P00015"], "direction": "in", "graph_message_id": "AAMk1"}
    odoo.script += [LOGIN_OK, rpc_ok([link_row])]
    link = await MailLinkRepo(client_factory()).link(
        po_id=15, graph_message_id="AAMk1", direction="in"
    )
    assert link.id == 1
    assert len([c for c in odoo.calls() if c[1] == "execute_kw"]) == 1
