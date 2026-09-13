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
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/home")
    return jsonResponse(200, {
      as_of: "2026-09-14",
      kpis: [
        { key: "service_level", value: 70, previous: null, unit: "pct", currency: null, detail: "2 scored supplier(s)" },
        { key: "late_orders", value: 1, previous: null, unit: "count", currency: null, detail: "0 silent RFQ(s)" },
        { key: "pending_approvals", value: 3, previous: 1.5, unit: "count", currency: null, detail: "median age 1.5 day(s)" },
        { key: "spend_month", value: 6514.44, previous: 500, unit: "money", currency: "USD", detail: null },
        { key: "ai_cost_month", value: 0.08, previous: 0.1, unit: "usd", currency: null, detail: null },
        { key: "automated_week", value: 2, previous: null, unit: "count", currency: null, detail: null },
      ],
      needs_you: [{ text: "#17 award: Round #1 on P00081 · 4,200 USD", path: "/approvals?id=17" }],
      pending: 3,
      late: 1,
      silent: 0,
      automated_week: 2,
    });
  if (url.pathname === "/api/briefing") return jsonResponse(200, { day: "2026-09-14", language: "en", since: "2026-09-13T12:00:00Z", sections: [], paragraph: "Two decisions wait for you.", counts: {}, emailed_to: [], created_at: "2026-09-14T07:30:00Z" });
  if (url.pathname === "/api/autonomy/actions") return jsonResponse(200, []);
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
}

describe("home", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the KPIs with their trend, the briefing paragraph and what needs you", async () => {
    window.history.replaceState(null, "", "/");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    const kpis = screen.getByRole("region", { name: "Key figures" });
    expect(within(kpis).getByText("70%")).toBeInTheDocument();
    expect(within(kpis).getByText("6,514 USD")).toBeInTheDocument();
    expect(within(kpis).getByText("before: 500 USD")).toBeInTheDocument();
    expect(within(kpis).getByText("$0.08")).toBeInTheDocument();
    expect(within(kpis).getByRole("link", { name: /Pending approvals/ })).toHaveAttribute("href", "/approvals");
    expect(await screen.findByText("Two decisions wait for you.")).toBeInTheDocument();
    const needs = screen.getByRole("region", { name: "Needs you" });
    expect(within(needs).getByRole("link", { name: /#17 award/ })).toHaveAttribute("href", "/approvals?id=17");
  });
});
