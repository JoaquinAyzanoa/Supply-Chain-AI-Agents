"""The three model-driven paths replayed from recorded DeepSeek answers.

``just llm-record`` re-runs these against the real provider and rewrites
``tests/fixtures/llm/supplier_comms.json``. Until recorded they skip.
Prompts and rendered context are part of the cassette key: changing a
prompt means re-recording, which is the intended review point.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sc_core.infra.settings import Settings, reset_settings_cache
from sc_core.llm import get_chat_client
from sc_core.schema.a2a import SupplierCommsTask
from supplier_comms import AGENT_NAME
from supplier_comms.models import InboundMeta
from supplier_comms.testing import SUPPLIER_EMAIL, FakePorts, demo_context

pytestmark = pytest.mark.llm_cassette

FIXTURE = Path("tests") / "fixtures" / "llm" / f"{AGENT_NAME}.json"
MSG = "AAMk-cassette"


@pytest.fixture
def real_or_replayed_chat() -> Any:
    reset_settings_cache()
    if os.environ.get("SC__LLM__RECORD_MODE") == "record":
        return get_chat_client(AGENT_NAME, settings=Settings())
    if not FIXTURE.exists():
        pytest.skip(f"{FIXTURE} not recorded yet; run `just llm-record`")
    return get_chat_client(
        AGENT_NAME, settings=Settings(_env_file=None, llm={"record_mode": "replay"})
    )


async def test_rfq_is_drafted_with_the_order_lines(
    make_agent: Any, ports: FakePorts, real_or_replayed_chat: Any
) -> None:
    agent = make_agent(chat=real_or_replayed_chat, auto_send_partner_ids=frozenset({42}))
    result = await agent.run(
        SupplierCommsTask(kind="send_rfq", case_id="cas_rfq", po_name="P00015")
    )
    assert result.status == "sent" and result.outbound is not None
    assert result.outbound.subject.startswith("[P00015]")
    draft = ports.drafts["draft1"]
    body = draft.html_body if hasattr(draft, "html_body") else str(draft)
    assert "Bomba" in body and "Manguera" in body and "Equipo de Compras" in body


async def test_eta_reply_proposes_the_new_date(
    make_agent: Any, ports: FakePorts, real_or_replayed_chat: Any
) -> None:
    ports.inbound[MSG] = (
        "Estimados, confirmamos que la orden P00015 llega el 20 de octubre de 2026. "
        "Quedamos atentos. Saludos, Ventas."
    )
    ports.metas[MSG] = InboundMeta(graph_message_id=MSG, sender_address=SUPPLIER_EMAIL)
    agent = make_agent(chat=real_or_replayed_chat)
    result = await agent.run(
        SupplierCommsTask(
            kind="handle_inbound", case_id="cas_eta", po_name="P00015", graph_message_id=MSG
        )
    )
    assert result.status == "awaiting_approval"
    assert result.classification is not None and result.classification.kind == "eta_update"
    assert result.extracted is not None and result.extracted.eta_date == date(2026, 10, 20)
    assert result.proposal is not None
    assert {c.after for c in result.proposal.changes if c.field == "date_planned"} == {"2026-10-20"}


async def test_unlinked_reply_is_attached_to_the_order_it_mentions(
    make_agent: Any, ports: FakePorts, real_or_replayed_chat: Any
) -> None:
    ports.contexts["P00016"] = demo_context(name="P00016").model_copy(
        update={
            "lines": [
                ports.contexts["P00015"]
                .lines[0]
                .model_copy(update={"id": 41, "product": 'Válvula de bola 1"', "product_id": 103})
            ]
        }
    )
    ports.inbound[MSG] = "Buenos días, la válvula de bola de 1 pulgada sale el lunes. Saludos."
    ports.metas[MSG] = InboundMeta(graph_message_id=MSG, sender_address=SUPPLIER_EMAIL)
    ports.partners[SUPPLIER_EMAIL] = 42
    agent = make_agent(chat=real_or_replayed_chat)
    result = await agent.run(
        SupplierCommsTask(
            kind="resolve_unlinked",
            case_id="cas_unl",
            graph_message_id=MSG,
            candidate_po_names=["P00015", "P00016"],
        )
    )
    assert result.chosen_po_name == "P00016"
    assert ports.links and ports.links[0]["po_id"] == 7 and ports.links[0]["confidence"] == "agent"
