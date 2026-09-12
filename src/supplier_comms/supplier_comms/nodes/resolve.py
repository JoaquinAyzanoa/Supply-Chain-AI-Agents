"""Resolve a message the linking rules could not attach to an order.

The model sees the supplier's text and the candidate orders (what the
director received in the unlinked event, plus anything the sender's
partner has open). A confident pick creates the inbound ``sc.mail.link``
with confidence ``agent`` and the run continues as ``handle_inbound``. No
pick, or a weak one, creates an ``unlinked_mail`` escalation for a person
and ends the run as ``escalated``; the person links the mail in Odoo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from sc_core.graph import ApprovalGateway, ApprovalRequest
from sc_core.infra.settings import LangfuseCfg
from sc_core.llm import ChatCompleter, complete_structured, system, user
from sc_core.prompts import get_prompt
from supplier_comms.models import InboundMeta, PoContext, UnlinkedResolution
from supplier_comms.nodes.common import finish, task_of
from supplier_comms.ports import AgentPorts
from supplier_comms.render import lines_table
from supplier_comms.state import Node

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
ESCALATION_STEP = "unlinked_mail"
PICK_THRESHOLD = 0.7


def make_resolve_unlinked(
    ports: AgentPorts,
    chat: ChatCompleter,
    approvals: ApprovalGateway,
    *,
    langfuse: LangfuseCfg | None,
    today: Callable[[], date],
) -> Node:
    async def resolve_unlinked(state: Any) -> dict[str, Any]:
        task = task_of(state)
        assert task.graph_message_id is not None
        meta = InboundMeta.model_validate(state["inbound_meta"])
        candidates = await _candidates(ports, task.candidate_po_names, meta)
        text = state.get("inbound_text") or ""

        resolution: UnlinkedResolution | None = None
        if candidates and text.strip():
            prompt = get_prompt("resolve_unlinked", local_dir=PROMPTS_DIR, cfg=langfuse)
            resolution = await complete_structured(
                chat,
                [
                    system(prompt.text),
                    user(_render(candidates, meta, text, today())),
                ],
                UnlinkedResolution,
                name="supplier_comms.resolve_unlinked",
                metadata={"prompt": prompt.name, "prompt_version": prompt.version},
            )
        chosen = _pick(resolution, candidates)
        if chosen is not None:
            assert resolution is not None
            await ports.link_inbound(chosen.id, meta, case_id=state["case_id"])
            logger.bind(po_name=chosen.name, confidence=resolution.confidence).info(
                "unlinked mail resolved by the agent"
            )
            return {
                "task": {**state["task"], "po_name": chosen.name},
                "chosen_po_name": chosen.name,
                "resolution": resolution.model_dump(mode="json"),
            }

        reason = resolution.reason if resolution else "sin candidatas o sin texto en el correo"
        partner_id = (
            await ports.partner_by_email(meta.sender_address) if meta.sender_address else None
        )
        update = await approvals.prepare(
            state,
            ESCALATION_STEP,
            ApprovalRequest(
                kind="unlinked_mail",
                summary=f"Correo de {meta.sender_address or 'remitente desconocido'} sin orden",
                payload={
                    "graph_message_id": meta.graph_message_id,
                    "sender_address": meta.sender_address,
                    "subject_token": meta.subject_token,
                    "candidates": [c.name for c in candidates],
                    "reason": reason,
                    "web_link": meta.web_link,
                },
                res_model="res.partner",
                res_id=partner_id,
            ),
        )
        pending = (update.get("pending_approvals") or [{}])[0]
        logger.bind(sender=meta.sender_address, candidates=len(candidates)).info(
            "unlinked mail escalated"
        )
        return {
            **update,
            **finish(
                "escalated",
                f"correo de {meta.sender_address or '-'} escalado a una persona: {reason}",
                resolution=resolution.model_dump(mode="json") if resolution else None,
                escalation_approval_id=pending.get("approval_id"),
            ),
        }

    return resolve_unlinked


async def _candidates(ports: AgentPorts, names: list[str], meta: InboundMeta) -> list[PoContext]:
    wanted = list(dict.fromkeys(names))
    if meta.subject_token and meta.subject_token not in wanted:
        wanted.append(meta.subject_token)
    if not wanted and meta.sender_address:
        partner_id = await ports.partner_by_email(meta.sender_address)
        if partner_id is not None:
            wanted = [po["name"] for po in await ports.open_pos(partner_id)]
    found: list[PoContext] = []
    for name in wanted:
        ctx = await ports.load_po(name)
        if ctx is not None:
            found.append(ctx)
    return found


def _pick(resolution: UnlinkedResolution | None, candidates: list[PoContext]) -> PoContext | None:
    if resolution is None or not resolution.po_name:
        return None
    if resolution.confidence < PICK_THRESHOLD:
        return None
    wanted = resolution.po_name.strip().upper()
    for ctx in candidates:
        if ctx.name.upper() == wanted:
            return ctx
    return None


def _render(candidates: list[PoContext], meta: InboundMeta, text: str, today: date) -> str:
    parts = [
        f"Fecha de hoy: {today.isoformat()}",
        f"Remitente: {meta.sender_address or '-'}",
        f"Token de orden en el asunto: {meta.subject_token or 'ninguno'}",
        "Órdenes candidatas:",
    ]
    for ctx in candidates:
        parts.append(
            f"- {ctx.name} | proveedor: {ctx.partner_name} | estado: {ctx.state} | "
            f"fecha prevista: {ctx.date_planned.isoformat() if ctx.date_planned else '-'}"
        )
        parts.append(lines_table(ctx))
    parts.append("Correo del proveedor:")
    parts.append(text.strip())
    return "\n".join(parts)
