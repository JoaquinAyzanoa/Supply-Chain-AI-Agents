import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { CaseDetail, CaseView } from "./api";

const CASES: CaseView[] = [
  {
    case_id: "case_a",
    code: "C00001",
    kind: "rfq",
    status: "awaiting_approval",
    po_name: "P00015",
    agent: "supplier_comms",
    summary: "Follow-up drafted, waiting for approval",
    created_at: "2026-09-10T10:00:00Z",
    updated_at: "2026-09-12T10:00:00Z",
  },
  {
    case_id: "case_b",
    code: "C00002",
    kind: "planning",
    status: "done",
    summary: "14 rules written",
    created_at: "2026-09-11T06:00:00Z",
    updated_at: "2026-09-11T06:05:00Z",
  },
];

const DETAIL: CaseDetail = {
  case: { ...CASES[0]!, trace_url: "http://langfuse/trace/tr1" },
  events: [
    { id: 1, at: "2026-09-10T10:00:00Z", kind: "event_received", payload: { event_type: "odoo.purchase_confirmed", source: "odoo" } },
    { id: 2, at: "2026-09-12T09:00:00Z", kind: "rule_fired", payload: { rule: "rfq_silent", reason: "no reply for 3 days", task: "follow_up" } },
    { id: 3, at: "2026-09-12T09:00:01Z", kind: "task_sent", payload: { task: "follow_up", agent: "supplier_comms", thread_id: "followup_p00015" } },
    { id: 4, at: "2026-09-12T09:00:20Z", kind: "result", payload: { status: "awaiting_approval", summary: "Reminder drafted", run_id: "run_1" } },
    { id: 5, at: "2026-09-12T09:00:21Z", kind: "approval_requested", payload: { approval_id: 7, agent: "supplier_comms" } },
    {
      id: 6,
      at: "2026-09-12T09:01:00Z",
      kind: "result",
      payload: {
        status: "failed",
        summary: "inventory_planning failed: The method 'x' does not exist",
        error: { code: "odoo_rpc_error", message: "The method 'x' does not exist", traceback: "Traceback..." },
      },
    },
  ],
  runs: [
    {
      run_id: "run_1",
      agent: "supplier_comms",
      case_id: "followup_p00015",
      po_name: "P00015",
      status: "awaiting_approval",
      model: "deepseek-v4-flash",
      started_at: "2026-09-12T09:00:01Z",
      finished_at: "2026-09-12T09:00:20Z",
      duration_seconds: 19,
      llm_calls: 3,
      input_tokens: 1200,
      output_tokens: 300,
      cost_usd: 0.0042,
      trace_url: "http://langfuse/trace/tr1",
    },
  ],
};

let requested: URL[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  requested.push(url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "vic@x.com", name: "Vic", role: "viewer" });
  if (url.pathname === "/api/cases") {
    const status = url.searchParams.get("status");
    return jsonResponse(200, status ? CASES.filter((c) => c.status === status) : CASES);
  }
  if (url.pathname.endsWith("/chat")) return jsonResponse(200, []);
  if (url.pathname === "/api/cases/case_a" || url.pathname === "/api/cases/C00001") return jsonResponse(200, DETAIL);
  return jsonResponse(404, { detail: "no" });
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

describe("cases", () => {
  beforeEach(() => {
    requested = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "vic@x.com", name: "Vic", role: "viewer" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("lists cases and filters by status through the URL", async () => {
    renderAt("/cases");
    expect(await screen.findByRole("link", { name: "C00001" })).toHaveAttribute("href", "/cases/C00001");
    expect(screen.getByText("14 rules written")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Status"), "awaiting_approval");
    await waitFor(() => expect(window.location.search).toBe("?status=awaiting_approval"));
    await waitFor(() => expect(screen.queryByText("14 rules written")).not.toBeInTheDocument());
    expect(requested.some((u) => u.pathname === "/api/cases" && u.searchParams.get("status") === "awaiting_approval")).toBe(true);
  });

  it("shows the timeline from event to approval and the agent runs", async () => {
    renderAt("/cases/case_a");
    const timeline = await screen.findByRole("list", { name: "Timeline" });
    const items = within(timeline).getAllByRole("listitem");
    expect(items).toHaveLength(6);
    expect(items[0]).toHaveTextContent("Purchase order confirmed · from Odoo");
    expect(items[1]).toHaveTextContent("rfq_silent · no reply for 3 days · follow_up");
    expect(items[2]).toHaveTextContent("Reminder · sent to the supplier agent");
    expect(items[3]).toHaveTextContent("Reminder drafted");
    expect(within(items[4]!).getByRole("link", { name: "approval #7" })).toHaveAttribute("href", "/?id=7");
    expect(items[5]).toHaveTextContent("Failed: The method 'x' does not exist");
    expect(items[5]).not.toHaveTextContent("Traceback");
    await userEvent.click(within(items[5]!).getByRole("button", { name: "Technical details" }));
    expect(items[5]).toHaveTextContent("odoo_rpc_error");
    expect(screen.getByText("Case C00001")).toBeInTheDocument();
    const traces = screen.getAllByRole("link", { name: "Trace" }); // the case and its run
    expect(traces).toHaveLength(2);
    expect(traces[0]).toHaveAttribute("href", "http://langfuse/trace/tr1");
    const runs = screen.getByRole("table");
    expect(runs).toHaveTextContent("deepseek-v4-flash");
    expect(runs).toHaveTextContent("1,200 / 300");
    expect(runs).toHaveTextContent("$0.0042");
    expect(runs).toHaveTextContent("19 s");
  });
});
