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
  if (url.pathname === "/api/ai")
    return jsonResponse(200, {
      days: 30,
      since: "2026-08-15T00:00:00Z",
      automation: [
        { kind: "send_email", automated: 3, decided: 1, rate: 0.75 },
        { kind: "award", automated: 0, decided: 2, rate: 0 },
      ],
      automation_rate: 0.5,
      decisions: 3,
      turnaround_hours_median: 1.2,
      edit_rate: 0.25,
      rejection_rate: 0.333,
      eta_error_days: 4,
      wape_by_class: [{ abc_class: "A", wape: 0.16, lines: 2 }],
      invoices_first_time: 2,
      invoices_total: 2,
      invoices_first_time_rate: 1,
      negotiation_savings: null,
      runs: 2,
      cost_usd: 0.08,
      cases_with_runs: 1,
      cost_per_case_usd: 0.08,
      by_agent: [{ agent: "sourcing", runs: 1, cost_usd: 0.06 }],
    });
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
}

describe("AI performance", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "vic@x.com", name: "Vic", role: "viewer" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the measures, the automation by kind and the cost by agent", async () => {
    window.history.replaceState(null, "", "/ai");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "AI performance" })).toBeInTheDocument();
    const measures = screen.getByRole("region", { name: "Measures" });
    expect(within(measures).getByText("50%")).toBeInTheDocument();
    expect(within(measures).getByText("1.2 h")).toBeInTheDocument();
    expect(within(measures).getByText("33%")).toBeInTheDocument();
    expect(within(measures).getByText("2 of 2")).toBeInTheDocument();
    expect(within(measures).getByText("$0.08")).toBeInTheDocument();
    const byKind = screen.getByRole("region", { name: "Automation by kind" });
    expect(within(byKind).getByText("75%")).toBeInTheDocument();
    expect(within(byKind).getByText("Quote round award")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Cost by agent" })).toHaveTextContent("sourcing · 1 run(s)");
    expect(screen.getByRole("region", { name: "Forecast error (WAPE) by class" })).toHaveTextContent("Class A · 16%");
  });
});
