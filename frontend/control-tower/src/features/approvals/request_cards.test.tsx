import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/i18n";
import { InternalRequestCard, PriceListCard } from "./RequestCards";
import { kindOf, priceListPayload, requestPayload } from "./types";

const REQUEST = requestPayload.parse({
  sender_address: "ana.torres@empresa.com",
  web_link: "https://outlook/int1",
  need_date: "2026-09-21",
  need_date_raw: "semana del 21",
  notes: "parada de planta",
  confidence: 0.9,
  unmatched: 1,
  items: [
    { description: "filtros hidráulicos HF-10", product_ref: "HF-10", qty: 20, product_id: 11, product: "[HF-10] Filtro hidráulico", match_confidence: 1, supplier_id: 42, supplier_name: "Proveedor Hidraulica", unit_price: 45, currency: "USD" },
    { description: "guantes de nitrilo", qty: 5, uom: "cajas", product_id: 12, product: "[GN-M] Guantes de nitrilo", match_confidence: 0.72, supplier_id: null, supplier_name: null },
    { description: "banderines de seguridad", qty: 3 },
  ],
});

const PRICE_LIST = priceListPayload.parse({
  partner_id: 8,
  partner_name: "Proveedor Hidraulica",
  source: "lista.xlsx:Hoja1",
  currency: "USD",
  changed: 1,
  unmatched: 1,
  rows: [
    { code: "CBEA-LHN", product_id: 1, product: "[CBEA-LHN] Válvula", current_price: 100, new_price: 104.16, currency: "USD", min_qty: 1, lead_days: 28, change_pct: 4.16, matched: true },
    { code: "RPEC-LAN", product_id: 4, product: "[RPEC-LAN] Alivio", current_price: null, new_price: 92.5, matched: true },
    { code: "VC-2", description: "Cartucho", new_price: 55, matched: false },
  ],
});

describe("internal request and price list cards", () => {
  it("shows the matched items, lets the buyer untick one and keeps unmatched rows out", async () => {
    const onToggle = vi.fn();
    render(
      <I18nProvider initial="en">
        <InternalRequestCard payload={REQUEST} accepted={new Set([0])} onToggle={onToggle} canAct />
      </I18nProvider>,
    );
    expect(screen.getByText("ana.torres@empresa.com")).toBeInTheDocument();
    expect(screen.getByText("parada de planta")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(within(rows[1]!).getByText("Proveedor Hidraulica")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("no supplier lists it")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("not in the catalogue")).toBeInTheDocument();
    const first = screen.getByRole("checkbox", { name: "Order filtros hidráulicos HF-10" });
    expect(first).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Order banderines de seguridad" })).toBeDisabled();
    await userEvent.click(first);
    expect(onToggle).toHaveBeenCalledWith(0, false);
    expect(kindOf({ kind: "internal_request" } as never)).toBe("internal_request");
  });

  it("shows the price diff with the change and only matched rows can be recorded", async () => {
    const onToggle = vi.fn();
    render(
      <I18nProvider initial="en">
        <PriceListCard payload={PRICE_LIST} accepted={new Set(["CBEA-LHN"])} onToggle={onToggle} canAct />
      </I18nProvider>,
    );
    expect(screen.getByText(/Proveedor Hidraulica sent lista.xlsx:Hoja1: 1 price\(s\) change, 1 row\(s\)/)).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]!).getByText("+4.2%")).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("min 1 · 28 d");
    expect(within(rows[2]!).getByText("no price yet")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("not in the catalogue")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Record VC-2" })).toBeDisabled();
    const second = screen.getByRole("checkbox", { name: "Record RPEC-LAN" });
    expect(second).not.toBeChecked();
    await userEvent.click(second);
    expect(onToggle).toHaveBeenCalledWith("RPEC-LAN", true);
    expect(kindOf({ kind: "price_list_update" } as never)).toBe("price_list_update");
  });
});
