import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "vic@x.com", name: "Vic", role: "viewer" });
  if (url.pathname === "/api/suppliers/45")
    return jsonResponse(200, {
      partner_id: 45,
      partner_name: "Proveedor Hidraulica",
      score: { partner_id: 45, partner_name: "Proveedor Hidraulica", score: 75.5, otif: 0.5, lead_time_mean_days: 42.3, response_hours_median: 2.8, scorecard: "Late on half of the deliveries." },
      orders: [{ po_id: 77, po_name: "P00077", state: "purchase", date_planned: "2026-09-06", amount_total: 2314.44, currency: "USD", receipt_status: "pending" }],
      prices: [{ product_id: 1, product: "[CBEA-LHN] Valvula", supplier_code: "VC-1", price: 104.16, currency: "USD", min_qty: 1, lead_days: 28, valid_from: null }],
      rounds: [],
      emails: [{ po_name: "P00077", direction: "in", at: "2026-09-12T09:00:00Z", web_link: "https://outlook/in", confidence: "exact" }],
      products: [{ product_id: 1, product: "[CBEA-LHN] Valvula", rank: 2, suppliers: 2, best_partner_name: "Hidraulica Alterna", best_score: 90 }],
    });
  if (url.pathname === "/api/sourcing/rounds") return jsonResponse(200, []);
  if (url.pathname === "/api/learning/profiles/45") return jsonResponse(200, { partner_id: 45, language: null, formality: null, greeting: null, sign_off: null, contacts: [], notes: "", facts: {}, updated_at: null, updated_by: null });
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
}

describe("supplier 360", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "vic@x.com", name: "Vic", role: "viewer" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("puts the scorecard, the orders, the prices with their rank and the email timeline on one page", async () => {
    window.history.replaceState(null, "", "/suppliers/45");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "Proveedor Hidraulica" })).toBeInTheDocument();
    const scorecard = screen.getByRole("region", { name: "Scorecard" });
    expect(within(scorecard).getByText("76")).toBeInTheDocument();
    expect(within(scorecard).getByText("50%")).toBeInTheDocument();
    expect(within(scorecard).getByText("Late on half of the deliveries.")).toBeInTheDocument();
    const orders = screen.getByRole("region", { name: "Orders (last 180 days)" });
    expect(within(orders).getByRole("link", { name: "P00077" })).toHaveAttribute("href", "/board?po=P00077");
    const prices = screen.getByRole("region", { name: "Price list" });
    expect(within(prices).getByText("#2 of 2")).toBeInTheDocument();
    expect(within(prices).getByText("VC-1")).toBeInTheDocument();
    const emails = screen.getByRole("region", { name: "Email timeline" });
    expect(within(emails).getByText("received")).toBeInTheDocument();
    expect(within(emails).getByRole("link", { name: "Draft in Outlook" })).toHaveAttribute("href", "https://outlook/in");
    expect(await screen.findByTestId("profile-45")).toBeInTheDocument();
  });
});
