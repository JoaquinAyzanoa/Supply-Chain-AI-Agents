import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { Schemas } from "@/api/client";

const CURRENT: Schemas["SettingsVersion"] = {
  version: 0,
  changed_by: "environment",
  changed_at: "2026-09-14T10:00:00Z",
  settings: {
    model_by_agent: {},
    rfq_no_reply_days: [3, 7],
    po_eta_request_before_days: 5,
    po_late_days: [1, 4],
    approval_stale_days: 2,
    approval_expire_days: 7,
    max_actions_per_run: 20,
    auto_send_partner_ids: [],
    auto_send_kinds: [],
    planning_service_level: null,
    planning_review_period_days: null,
    planning_max_coverage_days: null,
  },
};

const MODELS: Schemas["ModelOption"][] = [
  { name: "deepseek-v4-flash", provider: "deepseek", reasoning: false, input_usd_per_mtok: 0.14, output_usd_per_mtok: 0.28, configured: true },
  { name: "gpt-5.4", provider: "openai", reasoning: true, input_usd_per_mtok: 2, output_usd_per_mtok: 8, configured: false },
];

let saved: Record<string, unknown>[];
let me: { email: string; name: string; role: string };

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, me);
  if (url.pathname === "/api/settings" && request.method === "GET") return jsonResponse(200, CURRENT);
  if (url.pathname === "/api/settings/models") return jsonResponse(200, MODELS);
  if (url.pathname === "/api/settings/history") return jsonResponse(200, []);
  if (url.pathname === "/api/settings" && request.method === "PUT") {
    const body = (await request.json()) as Record<string, unknown>;
    saved.push(body);
    return jsonResponse(200, { ...CURRENT, version: 1, changed_by: "adm@x.com", settings: body.settings, note: body.note });
  }
  if (url.pathname === "/api/runs") return jsonResponse(200, []);
  if (url.pathname === "/api/runs/scheduler")
    return jsonResponse(200, [{ run_id: "s1", job_id: "po_followups", trigger: "cron", started_at: "2026-09-14T09:00:00Z", status: "ok", cron: "0 9 * * 1-5", summary: "3 tasks" }]);
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
}

function renderAt(path: string) {
  window.history.replaceState(null, "", path);
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

describe("settings and runs", () => {
  beforeEach(() => {
    saved = [];
    me = { email: "adm@x.com", name: "Adm", role: "admin" };
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "adm@x.com", name: "Adm", role: "admin" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("lets an admin pick a model and change the policy, saved as a new version", async () => {
    renderAt("/settings");
    const select = await screen.findByLabelText("Supplier agent");
    await waitFor(() => expect(select.querySelectorAll("option")).toHaveLength(3));
    expect(screen.getAllByRole("option", { name: /gpt-5.4/ })[0]).toBeDisabled(); // no API key here
    await userEvent.selectOptions(select, "deepseek-v4-flash");
    const days = screen.getByLabelText(/RFQ follow-ups after/);
    await userEvent.clear(days);
    await userEvent.type(days, "2, 5");
    await userEvent.click(screen.getByLabelText("Reminders"));
    await userEvent.click(screen.getByLabelText("Delivery date requests"));
    await userEvent.type(screen.getByLabelText("Note for the history"), "faster chasing");
    await userEvent.click(screen.getByRole("button", { name: "Save as a new version" }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0]).toEqual({
      note: "faster chasing",
      settings: {
        ...CURRENT.settings,
        model_by_agent: { supplier_comms: "deepseek-v4-flash" },
        rfq_no_reply_days: [2, 5],
        auto_send_kinds: ["follow_up", "request_eta"],
      },
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved as version 1.");
  });

  it("keeps settings away from a viewer and shows the scheduler runs", async () => {
    me = { email: "vic@x.com", name: "Vic", role: "viewer" };
    authStore.set({ token: "jwt", user: { email: "vic@x.com", name: "Vic", role: "viewer" } });
    renderAt("/settings");
    // the guard sends a viewer to the inbox
    await waitFor(() => expect(window.location.pathname).toBe("/"));
    cleanup();
    renderAt("/runs");
    expect(await screen.findByText("po_followups")).toBeInTheDocument();
    expect(screen.getByText("0 9 * * 1-5")).toBeInTheDocument();
  });
});
