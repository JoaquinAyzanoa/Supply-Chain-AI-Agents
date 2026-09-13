import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/i18n";
import { BillCard } from "./BillCard";
import { ScoreCard } from "./ScoreCard";
import { billPayload, kindOf, scoresPayload } from "./types";

vi.mock("@tanstack/react-router", () => ({ Link: (p: { children: React.ReactNode }) => <a>{p.children}</a> }));

describe("invoice and scorecard approval cards", () => {
  it("shows the verdict, the reasons and the line table of a held invoice", () => {
    const payload = billPayload.parse({
      po_name: "P00016",
      verdict: "hold",
      reasons: ["1 line(s) price variance"],
      invoice: { invoice_number: "F001-000123", invoice_date: "2026-10-03", currency: "USD", total: 1475 },
      lines: [
        { po_line_id: 28, product: "[CBEA-LHN] Válvula", invoice_description: "Valvula contrabalance", invoice_qty: 13, invoice_price: 107.28, po_qty: 13, po_price: 104.16, received_qty: 13, status: "price_variance", note: "billed 107.28, ordered 104.16 (+3.12)" },
        { po_line_id: 29, product: "[CBCA-LHN] Válvula", invoice_description: "[CBCA-LHN] Válvula", invoice_qty: 8, invoice_price: 88.04, po_qty: 8, po_price: 88.04, received_qty: 8, status: "ok" },
      ],
      expected_subtotal: 2058.4,
    });
    render(
      <I18nProvider initial="en">
        <BillCard payload={payload} />
      </I18nProvider>,
    );
    expect(screen.getByText("Does not match")).toBeInTheDocument();
    expect(screen.getByText("1 line(s) price variance")).toBeInTheDocument();
    expect(screen.getByText("F001-000123")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]!).getByText("price differs")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("billed 107.28, ordered 104.16 (+3.12)")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("ok")).toBeInTheDocument();
    expect(screen.getByText(/Nothing is posted/)).toBeInTheDocument();
    expect(kindOf({ kind: "vendor_bill" } as never)).toBe("vendor_bill");
    expect(kindOf({ kind: "supplier_score" } as never)).toBe("supplier_score");
    expect(kindOf({ kind: "orderpoint_change" } as never)).toBe("other");
  });

  it("lists the suppliers of a weekly run and opens the scorecard", async () => {
    const payload = scoresPayload.parse({
      run_id: "run_1",
      period: { start: "2025-09-13", end: "2026-09-13" },
      scores: [
        {
          partner_id: 45,
          partner_name: "Proveedor Hidraulica",
          score: 75.55,
          otif: 0.5,
          lead_time_mean_days: 42.28,
          lead_time_sigma_days: 3.43,
          response_hours_median: 2.81,
          quality_rate: 0,
          samples: { lines: 140, orders: 10, replies: 2 },
          scorecard: "Delivers in full but half the lines arrive after the promise; replies within three hours.",
          trends: ["OTIF down from 83% to 50%"],
        },
      ],
    });
    render(
      <I18nProvider initial="en">
        <ScoreCard payload={payload} />
      </I18nProvider>,
    );
    expect(screen.getByText("1 suppliers")).toBeInTheDocument();
    const row = screen.getAllByRole("row")[1]!;
    expect(row).toHaveTextContent("Proveedor Hidraulica");
    expect(row).toHaveTextContent("50%");
    expect(row).toHaveTextContent("42.3 d");
    expect(row).toHaveTextContent("2.8 h");
    expect(row).toHaveTextContent("OTIF down from 83% to 50%");
    await userEvent.click(screen.getByRole("button", { name: "Proveedor Hidraulica" }));
    expect(screen.getByText(/Delivers in full but half the lines/)).toBeInTheDocument();
  });
});
