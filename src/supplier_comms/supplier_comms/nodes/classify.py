"""Classify an inbound supplier message: quotation, eta_update, question or other."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from sc_core.schema.a2a import Classification
from supplier_comms.nodes.common import context_of
from supplier_comms.render import inbound_context
from supplier_comms.state import Node

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def make_classify(
    chat: ChatCompleter, *, langfuse: LangfuseCfg | None, today: Callable[[], date]
) -> Node:
    async def classify(state: Any) -> dict[str, Any]:
        ctx = context_of(state)
        tone = get_prompt("formats", cfg=langfuse).compile(po_name=ctx.name)
        prompt = get_prompt("classify_inbound", local_dir=PROMPTS_DIR, cfg=langfuse)
        result = await complete_structured(
            chat,
            [
                system("\n\n".join([tone, prompt.text])),
                user(
                    inbound_context(
                        ctx,
                        today(),
                        state.get("inbound_text") or "",
                        state.get("attachments_text") or [],
                    )
                ),
            ],
            Classification,
            name="supplier_comms.classify",
            metadata={"prompt": prompt.name, "prompt_version": prompt.version, "po_name": ctx.name},
        )
        logger.bind(po_name=ctx.name, kind=result.kind, confidence=result.confidence).info(
            "inbound classified"
        )
        return {"classification": result.model_dump(mode="json")}

    return classify
