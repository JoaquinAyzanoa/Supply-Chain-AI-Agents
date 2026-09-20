import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  if (url.pathname === "/api/learning/profiles/45" && request.method === "GET")
    return jsonResponse(200, { partner_id: 45, language: "es_PE", formality: "formal", greeting: "Estimados señores", sign_off: null, contacts: ["Carla Reyes"], notes: "Copy Carla on urgent orders.", facts: { reply_hours_median: 2.8 }, updated_at: null, updated_by: null });
  if (url.pathname === "/api/performance/scores")
    return jsonResponse(200, [
      {
        partner_id: 45,
        partner_name: "Proveedor Hidraulica",
        period_start: "2025-09-13",
        period_end: "2026-09-13",
        otif: 0.5,
        lead_time_mean_days: 42.28,
        lead_time_sigma_days: 3.43,
        promise_drift_days: 2.2,
        response_hours_median: 2.81,
        quality_rate: 0,
        price_cv: null,
        score: 75.55,
        samples: { lines: 140, orders: 10, replies: 2 },
        scorecard: "Half the lines arrive after the promise.",
        trends: [],
      },
    ]);
  return jsonResponse(404, { detail: "no" });
}

describe("suppliers page", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "vic@x.com", name: "Vic", role: "viewer" } });
    window.history.replaceState(null, "", "/suppliers");
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the latest scorecards", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "Suppliers" })).toBeInTheDocument();
    const row = (await screen.findAllByRole("row"))[1]!;
    expect(row).toHaveTextContent("Proveedor Hidraulica");
    expect(row).toHaveTextContent("140 lines received");
    expect(row).toHaveTextContent("50%");
    expect(screen.getAllByRole("link", { name: /Suppliers/ }).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Proveedor Hidraulica" }));
    const editor = await screen.findByTestId("profile-45");
    expect(within(editor).getByLabelText("Greeting")).toHaveValue("Estimados señores");
    expect(within(editor).getByLabelText("Contacts")).toHaveValue("Carla Reyes");
    expect(editor).toHaveTextContent("reply_hours_median 2.8");
    expect(within(editor).getByLabelText("Greeting")).toBeDisabled(); // a viewer reads only
  });
});
