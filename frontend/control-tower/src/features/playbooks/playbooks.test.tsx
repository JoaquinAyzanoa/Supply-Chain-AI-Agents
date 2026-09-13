import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import { PlaybookOutlook } from "./PlaybookBadge";

const LATE_ORDER = {
  name: "late_order",
  title: "Late order",
  description: "Ask the supplier for a new delivery date, wait two days, chase once more.",
  trigger: "po_late",
  active_runs: 1,
  steps: [
    { id: "ask_eta", label: "Ask the supplier for a new delivery date", kind: "agent", when: "not_received", agent: "supplier_comms", task: "request_eta", wait_days: null, until: null, action: null, active_runs: 0 },
    { id: "wait_reply", label: "Wait two days for a reply", kind: "wait", when: "always", agent: null, task: null, wait_days: 2, until: "reply_received", action: null, active_runs: 1 },
    { id: "chase", label: "Chase once more, firmly", kind: "agent", when: "no_reply", agent: "supplier_comms", task: "follow_up", wait_days: null, until: null, action: null, active_runs: 0 },
  ],
};
const POSITION = {
  run_id: 1,
  playbook: "late_order",
  title: "Late order",
  status: "waiting",
  step_id: "wait_reply",
  step_label: "Wait two days for a reply",
  step_index: 1,
  steps_total: 3,
  due_at: "2026-09-16T09:00:00Z",
  next_steps: ["Chase once more, firmly"],
  if_rejected: null,
};
const RUN = {
  id: 1,
  playbook: "late_order",
  case_id: "case_1",
  po_name: "P00077",
  partner_id: 8,
  status: "waiting",
  step_index: 1,
  due_at: "2026-09-16T09:00:00Z",
  waiting_for: "reply_received",
  started_by: "po_followups",
  started_at: "2026-09-14T09:00:00Z",
  updated_at: "2026-09-14T09:00:00Z",
  finished_at: null,
  summary: null,
};

type Me = { email: string; name: string; role: "admin" | "approver" | "viewer" };
let me: Me = { email: "ana@x.com", name: "Ana", role: "approver" };
let started: Record<string, unknown>[];
let cancelled: number[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, me);
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  if (url.pathname === "/api/playbooks") return jsonResponse(200, [LATE_ORDER]);
  if (url.pathname === "/api/playbooks/runs" && request.method === "GET") return jsonResponse(200, cancelled.length ? [] : [{ run: RUN, position: POSITION, steps: [] }]);
  if (url.pathname === "/api/playbooks/runs" && request.method === "POST") {
    const body = (await request.json()) as Record<string, unknown>;
    started.push(body);
    return jsonResponse(201, { run: { ...RUN, id: 2, po_name: body.po_name, status: "waiting_approval", step_index: 0 }, position: { ...POSITION, run_id: 2, status: "waiting_approval", step_id: "ask_eta", step_label: "Ask the supplier for a new delivery date", step_index: 0 }, steps: [] });
  }
  if (url.pathname === "/api/playbooks/runs/1/cancel") {
    cancelled.push(1);
    return jsonResponse(200, { run: { ...RUN, status: "cancelled", summary: "cancelled by ana@x.com" }, position: { ...POSITION, status: "cancelled" }, steps: [] });
  }
  if (url.pathname === "/api/playbooks/tick") return jsonResponse(200, { active: 1, moved: [1], at: "2026-09-14T10:00:00Z" });
  return jsonResponse(404, { detail: `unhandled ${request.method} ${url.pathname}` });
}

function renderApp() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider initial="en">
        <AuthProvider>
          <Routed />
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("playbooks page", () => {
  beforeEach(() => {
    started = [];
    cancelled = [];
    me = { email: "ana@x.com", name: "Ana", role: "approver" };
    window.history.replaceState(null, "", "/playbooks");
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "tok", user: me });
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows each plan's steps with counts, the active runs, and starts or cancels one", async () => {
    const user = userEvent.setup();
    renderApp();
    const card = await screen.findByRole("article", { name: "Late order" });
    expect(within(card).getByText("1 active")).toBeInTheDocument();
    expect(within(card).getByText("Wait two days for a reply")).toBeInTheDocument();
    expect(within(card).getByText(/wait 2 day\(s\) · or until the supplier replied/)).toBeInTheDocument();
    expect(within(card).getByText(/Supplier agent · Reminder · only if no reply from the supplier/)).toBeInTheDocument();

    // the active run: where it is and when it moves next
    const row = (await screen.findByRole("link", { name: "P00077" })).closest("tr")!;
    expect(within(row).getByText(/step 2 of 3 · Wait two days for a reply/)).toBeInTheDocument();
    expect(within(row).getByText("waiting")).toBeInTheDocument();

    // start one by hand
    await user.type(screen.getByLabelText("Order"), "p00073");
    await user.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(started).toEqual([{ playbook: "late_order", po_name: "P00073", partner_id: null }]));
    expect(await screen.findByRole("status")).toHaveTextContent("Late order started on P00073: Ask the supplier for a new delivery date");

    // cancel the running one
    await user.click(screen.getByRole("button", { name: "Cancel the playbook on P00077" }));
    await waitFor(() => expect(cancelled).toEqual([1]));
    expect(await screen.findByText(/No playbook is running/)).toBeInTheDocument();
  });

  it("hides start and cancel from viewers", async () => {
    me = { email: "vic@x.com", name: "Vic", role: "viewer" };
    authStore.set({ token: "tok", user: me });
    renderApp();
    await screen.findByRole("article", { name: "Late order" });
    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel the playbook/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Move runs now" })).not.toBeInTheDocument();
  });
});

describe("playbook outlook", () => {
  it("tells the step, the coming steps and what a rejection does", () => {
    render(
      <I18nProvider initial="en">
        <PlaybookOutlook position={{ ...POSITION, if_rejected: "the playbook stops and the order stays with a person" }} />
      </I18nProvider>,
    );
    expect(screen.getByTestId("playbook-badge")).toHaveTextContent("Late order · Wait two days for a reply · next move");
    expect(screen.getByText("Chase once more, firmly")).toBeInTheDocument();
    expect(screen.getByText(/the playbook stops and the order stays with a person/)).toBeInTheDocument();
  });
});
