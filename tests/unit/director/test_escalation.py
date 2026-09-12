"""Escalation: model summary, ``sc.approval`` of kind escalation, To-Do for the approver."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from director.escalation import EscalationPorts, OdooEscalator
from director.store import Case, MemoryCaseStore
from sc_core.infra.settings import LangfuseCfg, Settings, reset_settings_cache
from sc_core.llm.testing import ScriptedChatClient
from sc_core.shared.errors import ExternalServiceError

NO_LANGFUSE = LangfuseCfg(enabled=False)
SUMMARY = (
    "El proveedor no respondió a la solicitud de cotización de la orden P00015 en 8 días. "
    "Falta decidir si se insiste con el proveedor o se busca una alternativa. "
    "Proponemos llamar al proveedor y, si no hay respuesta en dos días, cotizar con el alterno."
)


class FakePorts:
    def __init__(self, po_ids: dict[str, int] | None = None) -> None:
        self.po_ids = po_ids or {"P00015": 15}
        self.created: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.fail_review = False

    async def po_id_for(self, po_name: str) -> int | None:
        return self.po_ids.get(po_name)

    async def create_escalation(
        self, *, summary: str, payload: dict[str, Any], case_id: str, po_id: int | None
    ) -> int:
        self.created.append(
            {"summary": summary, "payload": payload, "case_id": case_id, "po_id": po_id}
        )
        return 100 + len(self.created)

    async def schedule_review(
        self, *, res_model: str, res_id: int, summary: str, note_html: str, days: int
    ) -> None:
        if self.fail_review:
            raise ExternalServiceError("activity failed", service="odoo")
        self.reviews.append(
            {"res_model": res_model, "res_id": res_id, "summary": summary, "days": days}
        )


@pytest.fixture
def ports() -> FakePorts:
    ports = FakePorts()
    assert isinstance(ports, EscalationPorts)
    return ports


@pytest.fixture
def chat() -> ScriptedChatClient:
    return ScriptedChatClient()


@pytest.fixture
def cases() -> MemoryCaseStore:
    return MemoryCaseStore()


async def _case(cases: MemoryCaseStore, **overrides: Any) -> Case:
    case, _ = await cases.attach_or_create(
        kind=overrides.get("kind", "rfq"), po_name=overrides.get("po_name", "P00015")
    )
    await cases.add_event(case.case_id, "task_sent", {"task": "send_rfq", "thread_id": "t1"})
    await cases.add_event(case.case_id, "result", {"status": "sent", "summary": "RFQ enviada"})
    return await cases.update(case.case_id, trace_id="tr-abc", status="open")


async def test_failed_result_creates_escalation_with_summary_and_trace(
    ports: FakePorts, chat: ScriptedChatClient, cases: MemoryCaseStore, monkeypatch: Any
) -> None:
    from sc_core.infra import tracing

    monkeypatch.setattr(tracing, "_host", "http://langfuse.test")
    chat.responses.append(SUMMARY)
    escalator = OdooEscalator(chat, ports, cases, deadline_days=3, langfuse=NO_LANGFUSE)
    case = await _case(cases)

    escalation = await escalator.escalate(
        case, reason="no reply after 2 follow-ups and 8 days", details={"rule": "rfq_unanswered"}
    )

    assert escalation.approval_id == 101 and escalation.summary == SUMMARY
    assert escalation.trace_url == "http://langfuse.test/trace/tr-abc"
    [created] = ports.created
    assert created["po_id"] == 15 and created["case_id"] == case.case_id
    assert created["summary"] == SUMMARY
    payload = created["payload"]
    assert payload["reason"].startswith("no reply") and payload["details"] == {
        "rule": "rfq_unanswered"
    }
    assert payload["trace_url"] == escalation.trace_url
    assert payload["history"] == ["task_sent: send_rfq", "result: RFQ enviada"]
    [review] = ports.reviews
    assert review == {
        "res_model": "purchase.order",
        "res_id": 15,
        "summary": "Escalation P00015",
        "days": 3,
    }
    # exactly one model call, fed with the reason and the history, never with an email body
    [call] = chat.calls
    assert call.options["name"] == "escalation_summary"
    prompt = call.messages[0]["contents"][0]["text"]
    assert "rfq_unanswered" in prompt and "RFQ enviada" in prompt and "P00015" in prompt


async def test_model_failure_falls_back_to_the_reason(
    ports: FakePorts, chat: ScriptedChatClient, cases: MemoryCaseStore
) -> None:
    chat.responses.append(ExternalServiceError("provider down", service="llm"))
    escalator = OdooEscalator(chat, ports, cases, langfuse=NO_LANGFUSE)
    case = await _case(cases)
    escalation = await escalator.escalate(case, reason="agent unreachable")
    assert escalation.summary == "agent unreachable" and escalation.approval_id == 101
    assert ports.created[0]["summary"] == "agent unreachable"


async def test_case_without_order_hangs_the_review_on_the_approval(
    ports: FakePorts, chat: ScriptedChatClient, cases: MemoryCaseStore
) -> None:
    chat.responses.append("Resumen.")
    escalator = OdooEscalator(chat, ports, cases, langfuse=NO_LANGFUSE)
    case, _ = await cases.attach_or_create(kind="unlinked", po_name=None)
    escalation = await escalator.escalate(case, reason="unknown sender")
    assert ports.created[0]["po_id"] is None
    assert ports.reviews[0] == {
        "res_model": "sc.approval",
        "res_id": escalation.approval_id,
        "summary": "Escalation unlinked",
        "days": 2,
    }


async def test_review_failure_does_not_lose_the_escalation(
    ports: FakePorts, chat: ScriptedChatClient, cases: MemoryCaseStore
) -> None:
    ports.fail_review = True
    chat.responses.append("Resumen.")
    escalator = OdooEscalator(chat, ports, cases, langfuse=NO_LANGFUSE)
    case = await _case(cases)
    escalation = await escalator.escalate(case, reason="x")
    assert escalation.approval_id == 101 and ports.reviews == []


# --- replayed model ------------------------------------------------------------------

FIXTURE = Path("tests") / "fixtures" / "llm" / "director.json"


@pytest.mark.llm_cassette
async def test_summary_from_the_recorded_model(ports: FakePorts, cases: MemoryCaseStore) -> None:
    """The real model (DeepSeek) writes three sentences; replayed from ``director.json``."""
    from sc_core.llm import get_chat_client

    reset_settings_cache()
    if os.environ.get("SC__LLM__RECORD_MODE") == "record":
        chat = get_chat_client("director", settings=Settings())
    elif FIXTURE.exists():
        chat = get_chat_client(
            "director", settings=Settings(_env_file=None, llm={"record_mode": "replay"})
        )
    else:
        pytest.skip(f"{FIXTURE} not recorded yet; run `just llm-record`")
    escalator = OdooEscalator(chat, ports, cases, langfuse=NO_LANGFUSE)
    case = await _case(cases)
    escalation = await escalator.escalate(
        case,
        reason="no reply to the RFQ after 2 follow-ups and 8 days",
        details={"rule": "rfq_unanswered", "days": 8},
    )
    sentences = [s for s in escalation.summary.replace("\n", " ").split(". ") if s.strip()]
    assert 2 <= len(sentences) <= 4, escalation.summary
    assert "P00015" in escalation.summary
    assert ports.created[0]["summary"] == escalation.summary
