"""Talk to the director about the whole department (phase 11 S7).

``POST /api/assistant`` answers from the desk's facts with citations; an
instruction comes back as a plan the person confirms once
(``POST /api/assistant/{message_id}/confirm``), after which every step runs
through the agents and their approvals. The conversation is per person.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected
from loguru import logger
from pydantic import Field

from director.api.auth import Approver, Principal, Viewer
from director.desk.assistant import (
    AssistantMessage,
    AssistantStore,
    DepartmentAssistant,
    PlanRunner,
)
from sc_core.llm.structured import StructuredOutputFailed
from sc_core.schema.base import StrictModel
from sc_core.shared.errors import ScError

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AskRequest(StrictModel):
    text: str = Field(min_length=1, max_length=2000)


class AssistantTurn(StrictModel):
    messages: list[AssistantMessage]


@router.get("", response_model=list[AssistantMessage])
async def read_conversation(
    principal: Principal = Viewer,
    store: AssistantStore = Injected(AssistantStore),  # type: ignore[type-abstract]
) -> list[AssistantMessage]:
    return await store.messages(principal.email)


@router.post("", response_model=AssistantTurn)
async def ask(
    body: AskRequest,
    principal: Principal = Viewer,
    store: AssistantStore = Injected(AssistantStore),  # type: ignore[type-abstract]
    assistant_: DepartmentAssistant = Injected(DepartmentAssistant),
) -> AssistantTurn:
    history = await store.messages(principal.email)
    asked = await store.add(principal.email, role="user", text=body.text, by=principal.email)
    try:
        answer, citations = await assistant_.answer(body.text, history=history)
    except (ScError, StructuredOutputFailed) as exc:
        logger.bind(by=principal.email).warning("department assistant failed: {}", exc)
        raise HTTPException(
            status_code=502, detail="the director could not answer right now"
        ) from exc
    answered = await store.add(
        principal.email,
        role="director",
        text=answer.reply,
        citations=citations,
        plan=answer.plan,
    )
    return AssistantTurn(messages=[asked, answered])


@router.post("/{message_id}/confirm", response_model=AssistantMessage)
async def confirm_plan(
    message_id: int,
    principal: Principal = Approver,
    store: AssistantStore = Injected(AssistantStore),  # type: ignore[type-abstract]
    runner: PlanRunner = Injected(PlanRunner),
) -> AssistantMessage:
    message = await store.get(principal.email, message_id)
    if message is None or message.plan is None:
        raise HTTPException(status_code=404, detail="no plan on that message")
    if message.plan_status != "proposed":
        raise HTTPException(status_code=409, detail=f"plan already {message.plan_status}")
    outcomes = await runner.execute(message.plan, by=principal)
    outcome = "\n".join(outcomes)
    logger.bind(by=principal.email, steps=len(outcomes)).info("assistant plan executed")
    return await store.set_plan_status(message_id, "confirmed", outcome=outcome[:4000])


@router.post("/{message_id}/dismiss", response_model=AssistantMessage)
async def dismiss_plan(
    message_id: int,
    principal: Principal = Approver,
    store: AssistantStore = Injected(AssistantStore),  # type: ignore[type-abstract]
) -> AssistantMessage:
    message = await store.get(principal.email, message_id)
    if message is None or message.plan is None:
        raise HTTPException(status_code=404, detail="no plan on that message")
    if message.plan_status != "proposed":
        raise HTTPException(status_code=409, detail=f"plan already {message.plan_status}")
    return await store.set_plan_status(message_id, "dismissed")
