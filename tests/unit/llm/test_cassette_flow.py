"""A model call that runs offline from a recorded cassette.

Marked ``llm_cassette``: ``just llm-record`` reruns exactly these tests with
``SC__LLM__RECORD_MODE=record`` against the real provider and writes
``tests/fixtures/llm/<agent>.json``; the normal suite replays that file and
skips (never fails) while it has not been recorded yet.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from sc_core.infra.settings import Settings, reset_settings_cache
from sc_core.llm import complete_structured, get_chat_client, system, user

pytestmark = pytest.mark.llm_cassette

AGENT = "cassette_demo"
FIXTURE = Path("tests") / "fixtures" / "llm" / f"{AGENT}.json"


class Eta(BaseModel):
    eta_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    confidence: float = Field(ge=0, le=1)


@pytest.fixture
def settings() -> Settings:
    reset_settings_cache()
    if os.environ.get("SC__LLM__RECORD_MODE") == "record":
        return Settings()  # real provider; key comes from .env
    if not FIXTURE.exists():
        pytest.skip(f"{FIXTURE} not recorded yet; run `just llm-record`")
    return Settings(_env_file=None, llm={"record_mode": "replay"})


async def test_structured_eta_from_cassette(settings: Settings) -> None:
    client = get_chat_client(AGENT, settings=settings)
    answer = await complete_structured(
        client,
        [
            system("Extrae la fecha de entrega del correo del proveedor. Hoy es 2026-09-12."),
            user("Confirmamos que la orden P00015 llega el 20 de octubre de 2026."),
        ],
        Eta,
        name="cassette.eta",
    )
    assert answer.eta_date == "2026-10-20" and answer.confidence > 0.5
