"""Structured outputs: a completion that must validate against a Pydantic model.

When the model supports JSON-schema output, the schema is sent as the
response format so the provider constrains generation. Otherwise the schema
is described in an instruction and ``json_object`` mode is requested. In
both cases the text is validated with Pydantic here, because provider
guarantees vary; on a validation error the model gets one more attempt with
the error appended, then ``StructuredOutputFailed`` is raised.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from sc_core.llm.client import ChatCompleter, MessageLike, assistant, to_message, user
from sc_core.shared.errors import ValidationFailed

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class StructuredOutputFailed(ValidationFailed):
    code = "structured_output_failed"


def strip_fences(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


def parse_as[T: BaseModel](schema: type[T], text: str) -> T:
    """Validate model text as ``schema`` (code fences tolerated)."""
    return schema.model_validate_json(strip_fences(text))


async def complete_structured[T: BaseModel](
    client: ChatCompleter,
    messages: Sequence[MessageLike],
    schema: type[T],
    *,
    max_attempts: int = 2,
    temperature: float | None = 0.0,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    history = [to_message(m) for m in messages]
    if client.spec.json_schema_output:
        response_format: type[BaseModel] | dict[str, Any] = schema
    else:
        response_format = {"type": "json_object"}
        history.append(
            user(
                "Answer only with a valid JSON object that satisfies this schema, "
                "with no additional text:\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False)
            )
        )

    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        result = await client.complete(
            history,
            temperature=temperature,
            response_format=response_format,
            name=name or f"structured.{schema.__name__}",
            metadata={**(metadata or {}), "attempt": attempt, "schema": schema.__name__},
        )
        try:
            return parse_as(schema, result.text)
        except (ValidationError, ValueError) as exc:
            errors.append(str(exc)[:1000])
            history.append(assistant(result.text))
            history.append(
                user(
                    "The previous answer is not valid. Fix it and answer only with the JSON.\n"
                    f"Error: {str(exc)[:1500]}"
                )
            )
    raise StructuredOutputFailed(
        f"model output did not match {schema.__name__} after {max_attempts} attempts",
        details={"schema": schema.__name__, "errors": errors},
    )
