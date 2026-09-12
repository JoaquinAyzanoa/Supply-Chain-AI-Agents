"""Read-only tools the model may call while drafting. Writes never happen here."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from sc_core.graph import Tool, ToolBox
from supplier_comms.ports import AgentPorts


class PoLinesArgs(BaseModel):
    po_name: str = Field(description="Order name, for example P00015")


class PriceHistoryArgs(BaseModel):
    partner_id: int = Field(description="Supplier id in Odoo")
    product_id: int | None = Field(default=None, description="Product id; empty = all")


class OpenPosArgs(BaseModel):
    partner_id: int = Field(description="Supplier id in Odoo")


def build_toolbox(ports: AgentPorts) -> ToolBox:
    async def get_po_lines(po_name: str) -> list[dict[str, Any]]:
        ctx = await ports.load_po(po_name)
        if ctx is None:
            return []
        return [line.model_dump(mode="json") for line in ctx.lines]

    async def get_supplier_price_history(
        partner_id: int, product_id: int | None = None
    ) -> list[dict[str, Any]]:
        return await ports.price_history(partner_id, product_id)

    async def get_open_pos_for_supplier(partner_id: int) -> list[dict[str, Any]]:
        return await ports.open_pos(partner_id)

    return ToolBox(
        [
            Tool(
                "get_po_lines",
                "Lines of an order: product, quantity, unit, price and planned date.",
                PoLinesArgs,
                get_po_lines,
            ),
            Tool(
                "get_supplier_price_history",
                "Prices previously agreed with the supplier (price lists in Odoo).",
                PriceHistoryArgs,
                get_supplier_price_history,
            ),
            Tool(
                "get_open_pos_for_supplier",
                "Open orders with the supplier: name, state, planned date, amount.",
                OpenPosArgs,
                get_open_pos_for_supplier,
            ),
        ]
    )
