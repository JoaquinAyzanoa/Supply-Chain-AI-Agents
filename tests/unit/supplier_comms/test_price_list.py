"""Price lists in attachments: parsed in code, diffed against Odoo, written only after approval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sc_core.llm.testing import ScriptedChatClient
from sc_core.mail.models import Attachment
from sc_core.mail.tables import TableData, read_tables
from sc_core.schema.a2a import QuotationData, SupplierCommsTask
from supplier_comms.models import InboundMeta
from supplier_comms.pricelists import build_diff, find_header, parse_number, parse_price_list
from supplier_comms.testing import FakePorts
from tests.unit.graph.toy import FakeApprovalPorts

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "mail"
MSG = "AAMk-pricelist-1"


def test_numbers_in_both_conventions_parse() -> None:
    assert parse_number("1,234.50") == 1234.5
    assert parse_number("1.234,50") == 1234.5
    assert parse_number("12,5") == 12.5
    assert parse_number("USD 98.50") == 98.5
    assert parse_number("S/ 1,050") == 1050.0
    assert parse_number("n/a") is None and parse_number("") is None


def test_the_header_is_found_in_spanish_and_english() -> None:
    rows = [["Lista de precios"], [], ["Código", "Descripción", "Precio USD", "Cant. mínima"]]
    assert find_header(rows) == (2, {"code": 0, "description": 1, "price": 2, "min_qty": 3})
    assert find_header([["Item", "Part number", "Unit price"]]) == (0, {"code": 0, "price": 2})
    assert find_header([["Nombre", "Telefono"]]) is None


def test_the_excel_fixture_becomes_rows_with_currency_min_qty_and_lead_time() -> None:
    data = (FIXTURES / "price_list_hidraulica.xlsx").read_bytes()
    [table] = read_tables(
        Attachment(
            name="price_list_hidraulica.xlsx",
            content_type="application/octet-stream",
            size=len(data),
            data=data,
        )
    )
    parsed = parse_price_list(table)
    assert parsed is not None and parsed.currency == "USD"
    assert parsed.source == "price_list_hidraulica.xlsx:Lista 2026"
    assert [(r.code, r.price, r.min_qty, r.lead_days) for r in parsed.rows] == [
        ("CBEA-LHN", 104.16, 1.0, 28),
        ("RPEC-LAN", 92.5, 5.0, 21),
        ("VC-2", 55.0, 1.0, 14),
    ]


def test_the_csv_fixture_reads_semicolons_and_a_currency_column() -> None:
    data = (FIXTURES / "price_list_hidraulica.csv").read_bytes()
    [table] = read_tables(
        Attachment(name="lista.csv", content_type="text/csv", size=len(data), data=data)
    )
    parsed = parse_price_list(table)
    assert parsed is not None
    assert [(r.code, r.price, r.currency) for r in parsed.rows] == [
        ("CBEA-LHN", 1234.5, "USD"),
        ("RPEC-LAN", 92.5, "USD"),
    ]
    # a spreadsheet without a price column is not a price list
    assert (
        parse_price_list(TableData(name="x", rows=[["Nombre", "Cargo"], ["Ana", "Ventas"]])) is None
    )


def test_the_diff_matches_by_our_code_or_the_suppliers_and_counts_the_rest() -> None:
    parsed = parse_price_list(
        TableData(
            name="lista.xlsx:Hoja1",
            rows=[
                ["Codigo", "Precio"],
                ["CBEA-LHN", "104.16"],
                ["VS-9", "70"],  # the supplier's own code for our RPEC-LAN
                ["ZZ-1", "5"],
            ],
        )
    )
    assert parsed is not None
    current = [
        {
            "product": "[CBEA-LHN] Válvula",
            "product_id": 1,
            "product_code": None,
            "price": 100.0,
            "currency": "USD",
        },
        {
            "product": "[RPEC-LAN] Alivio",
            "product_id": 4,
            "product_code": "VS-9",
            "price": 70.0,
            "currency": "USD",
        },
    ]
    catalogue = {"CBEA-LHN": (1, "[CBEA-LHN] Válvula"), "RPEC-LAN": (4, "[RPEC-LAN] Alivio")}
    diff = build_diff(
        parsed,
        partner_id=8,
        partner_name="Proveedor Hidraulica",
        current=current,
        lookup=catalogue.get,
    )
    assert diff.unmatched == 1
    first, second, third = diff.rows
    assert first.matched and first.current_price == 100.0 and first.change_pct == 4.16
    assert second.matched and second.product_id == 4 and second.change_pct == 0.0
    assert not third.matched and third.product_id is None


def _prepare(ports: FakePorts) -> None:
    ports.inbound[MSG] = "Estimados, adjuntamos nuestra lista de precios vigente."
    ports.metas[MSG] = InboundMeta(
        graph_message_id=MSG,
        sender_address="ventas.hidraulica.sc@gmail.com",
        has_attachments=True,
        web_link="https://outlook/pl1",
    )
    ports.tables[MSG] = [
        TableData(
            name="lista.xlsx:Hoja1",
            rows=[
                ["Codigo", "Descripcion", "Precio USD", "Plazo (dias)"],
                ["CBEA-LHN", "Valvula contrabalance", "104.16", "28"],
                ["RPEC-LAN", "Valvula alivio", "92,50", "21"],
                ["VC-2", "Cartucho", "55", "14"],
            ],
        )
    ]
    ports.prices = [
        {
            "product": "[CBEA-LHN] Válvula",
            "product_id": 1,
            "product_code": None,
            "price": 100.0,
            "currency": "USD",
        },
    ]
    ports.products_by_code["CBEA-LHN"] = (1, "[CBEA-LHN] Válvula")
    ports.products_by_code["RPEC-LAN"] = (4, "[RPEC-LAN] Alivio")
    ports.template_ids = {1: 1001, 4: 1004}


async def test_a_price_list_on_an_order_thread_pauses_on_approval_and_writes_accepted_rows(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    agent = make_agent()
    task = SupplierCommsTask(
        kind="handle_inbound", case_id="case_pl1", po_name="P00015", graph_message_id=MSG
    )
    paused = await agent.run(task)
    assert paused.status == "awaiting_approval" and paused.outcome.approval_id == 101
    assert chat.calls == []  # a spreadsheet needs no model
    created = approval_ports.created[0]
    assert created["kind"] == "price_list_update" and created["po_id"] == 7
    payload = created["payload"]
    assert payload["partner_id"] == 42 and payload["changed"] == 2 and payload["unmatched"] == 1
    assert [r["code"] for r in payload["rows"]] == ["CBEA-LHN", "RPEC-LAN", "VC-2"]
    assert payload["rows"][0]["change_pct"] == 4.16 and payload["rows"][2]["matched"] is False
    assert payload["facts"]["change_pct"] == 4.16
    assert (
        "Price list from Proveedor Hidraulica: 2 price(s) change, 1 not in the catalogue"
        in created["summary"]
    )
    assert paused.price_list is not None and paused.price_list.source == "lista.xlsx:Hoja1"
    assert ports.price_upserts == []

    done = await agent.resume(
        "case_pl1",
        {
            "approval_id": 101,
            "status": "approved",
            "resolved_by": "Ana",
            "reason": None,
            "details": {"accepted_codes": ["CBEA-LHN", "VC-2"]},
        },
    )
    assert done.status == "applied"
    assert (
        "1 price(s) from Proveedor Hidraulica's list recorded (1 left out)" in done.outcome.summary
    )
    [upsert] = ports.price_upserts
    assert upsert["partner_id"] == 42 and upsert["product_tmpl_id"] == 1001
    assert upsert["price"] == 104.16 and upsert["currency_id"] == 2 and upsert["lead_days"] == 28
    assert ports.notes and ports.notes[-1][0] == 7


async def test_a_price_list_without_an_order_is_diffed_for_the_known_partner(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    ports.partners["ventas.hidraulica.sc@gmail.com"] = 42
    task = SupplierCommsTask(kind="resolve_unlinked", case_id="case_pl2", graph_message_id=MSG)
    paused = await make_agent().run(task)
    assert paused.status == "awaiting_approval"
    created = approval_ports.created[0]
    assert created["kind"] == "price_list_update" and created["po_id"] is None
    assert created["res_model"] == "res.partner" and created["res_id"] == 42
    assert chat.calls == []


async def test_a_strangers_spreadsheet_follows_the_normal_path(
    make_agent: Any, ports: FakePorts, chat: ScriptedChatClient, approval_ports: FakeApprovalPorts
) -> None:
    _prepare(ports)
    ports.partners.clear()
    chat.responses.append(QuotationData(lines=[], confidence=0.3))  # nothing quoted in the text
    task = SupplierCommsTask(kind="resolve_unlinked", case_id="case_pl3", graph_message_id=MSG)
    result = await make_agent().run(task)
    # no partner, no candidates: the unknown-sender flow escalates as before
    assert result.status == "escalated" and approval_ports.created[0]["kind"] == "unlinked_mail"
