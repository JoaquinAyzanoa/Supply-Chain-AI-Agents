"""ToolBox: definitions, validation, error mapping and result rendering."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from sc_core.graph import ToolBox, ToolCall, ToolError, tool
from sc_core.shared.errors import NotFound


class LinesArgs(BaseModel):
    po_name: str = Field(description="Order name, e.g. P00015")


class Line(BaseModel):
    product: str
    qty: float


@tool("get_po_lines", "Lines of a purchase order", LinesArgs)
async def get_po_lines(po_name: str) -> list[Line]:
    if po_name == "P00404":
        raise NotFound("no such order")
    return [Line(product="Bomba", qty=2)]


@pytest.fixture
def box() -> ToolBox:
    return ToolBox([get_po_lines])


def test_definitions_follow_openai_function_schema(box: ToolBox) -> None:
    [definition] = box.definitions()
    assert definition["type"] == "function"
    fn = definition["function"]
    assert fn["name"] == "get_po_lines" and "po_name" in fn["parameters"]["properties"]
    assert fn["parameters"]["required"] == ["po_name"]
    assert box.names == ["get_po_lines"]


async def test_call_validates_and_serialises(box: ToolBox) -> None:
    result = await box.call(ToolCall(id="c1", name="get_po_lines", arguments='{"po_name": "P1"}'))
    assert result == [Line(product="Bomba", qty=2)]
    assert ToolBox.result_text(result) == '[{"product": "Bomba", "qty": 2.0}]'


async def test_errors_become_tool_results_not_exceptions(box: ToolBox) -> None:
    unknown = await box.call(ToolCall(id="c", name="nope", arguments="{}"))
    assert isinstance(unknown, ToolError) and unknown.code == "unknown_tool"
    bad_args = await box.call(ToolCall(id="c", name="get_po_lines", arguments='{"x": 1}'))
    assert isinstance(bad_args, ToolError) and bad_args.code == "invalid_arguments"
    domain = await box.call(
        ToolCall(id="c", name="get_po_lines", arguments='{"po_name": "P00404"}')
    )
    assert isinstance(domain, ToolError) and domain.code == "not_found"
    assert '"error"' in ToolBox.result_text(domain)


def test_duplicate_name_rejected() -> None:
    with pytest.raises(ValueError, match="twice"):
        ToolBox([get_po_lines, get_po_lines])
