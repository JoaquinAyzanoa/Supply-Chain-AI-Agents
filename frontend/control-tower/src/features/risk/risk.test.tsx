import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { ProductRisk, RiskReport } from "./api";

function product(id: number, over: Partial<ProductRisk> = {}): ProductRisk {
  return {
    product_id: id,
    product_ref: `HYD-${id}`,
    product_name: "Bomba hidráulica",
    on_hand: 4,
    reserved: 0,
    position: 4,
    incoming_30: 0,
    incoming_60: 10,
    daily_mean: 0.5,
    daily_sigma: 0.3,
    forecast_method: "tsb",
    days_of_cover: 8,
    p_stockout_30: 0.62,
    p_stockout_60: 0.8,
    open_po_names: [],
    late_po_names: [],
    suggested_qty: 12,
    exposure: 500,
    unit_price: 40,
    ...over,
  };
}

const REPORT: RiskReport = {
  as_of: "2026-09-14",
  warehouse_code: "WH",
  products: [product(101, { open_po_names: ["P00080"], late_po_names: ["P00080"] }), product(102, { p_stockout_30: 0.1, p_stockout_60: 0.3, suggested_qty: 30 })],
  suppliers: [{ partner_id: 8, partner_name: "Proveedor Hidraulica", open_lines: 3, overdue_lines: 1, expected_late_lines: 1.4, otif: 0.5, lead_time_sigma_days: 4, exposure: 1200 }],
  cash_exposure: 1700,
  at_risk_30: 1,
};

let acted: { path: string; body: Record<string, unknown> }[];
let me: { email: string; name: string; role: string };

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, me);
  if (url.pathname === "/api/risk") return jsonResponse(200, REPORT);
  if (url.pathname.startsWith("/api/risk/") && request.method === "POST") {
    acted.push({ path: url.pathname, body: (await request.json()) as Record<string, unknown> });
    return jsonResponse(202, { case_id: "risk_1", case_code: "C00040", thread_id: "risk_1", status: "sent", summary: "quote round sent to 2 suppliers" });
  }
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
}

function renderRisk() {
  window.history.replaceState(null, "", "/risk");
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider initial="en">
        <AuthProvider>
          <Routed />
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("risk radar", () => {
  beforeEach(() => {
    acted = [];
    me = { email: "ana@x.com", name: "Ana", role: "approver" };
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: me as never });
  });
  afterEach(() => vi.restoreAllMocks());

  it("ranks the products, shows the late order and starts sourcing in one click", async () => {
    renderRisk();
    expect(await screen.findByRole("heading", { name: "Risk radar" })).toBeInTheDocument();
    expect(screen.getByText("1 products above 50% at 30 days")).toBeInTheDocument();
    expect(screen.getByText("62%")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "P00080 (late)" })).toBeInTheDocument();
    expect(screen.getByText("Proveedor Hidraulica")).toBeInTheDocument();
    // the late order behind the risk: source it elsewhere; the other product needs a round
    const alternate = screen.getByRole("button", { name: "Act on HYD-101" });
    expect(alternate).toHaveTextContent("Source elsewhere");
    expect(screen.getByRole("button", { name: "Act on HYD-102" })).toHaveTextContent("Request quotes");
    await userEvent.click(alternate);
    await waitFor(() => expect(acted).toHaveLength(1));
    expect(acted[0]).toEqual({ path: "/api/risk/101/act", body: { qty: null, po_name: null } });
    expect(await screen.findByRole("status")).toHaveTextContent("quote round sent to 2 suppliers");
  });

  it("shows no buttons to a viewer", async () => {
    me = { email: "vic@x.com", name: "Vic", role: "viewer" };
    authStore.set({ token: "jwt", user: me as never });
    renderRisk();
    expect(await screen.findByRole("heading", { name: "Risk radar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Act on/ })).toBeNull();
  });
});
