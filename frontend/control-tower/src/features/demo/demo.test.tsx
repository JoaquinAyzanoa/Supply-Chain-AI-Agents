import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";

const STEPS = [
  { key: "late_order_eta", title: "A late order gets a delivery-date request", say: "The department noticed.", click: "Approvals: the email.", needs: "none" },
  { key: "supplier_eta_reply", title: "The supplier answers with a new date", say: "From its own mailbox.", click: "Approvals; Board.", needs: "mailbox" },
  { key: "briefing", title: "The briefing the next morning", say: "What happened.", click: "Briefing.", needs: "none" },
];

let view: Record<string, unknown>;
let posted: { path: string; body: unknown }[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/demo" && request.method === "GET") return jsonResponse(200, view);
  if (url.pathname === "/api/demo/reset") {
    posted.push({ path: url.pathname, body: null });
    view = { ...view, started_at: "2026-09-14T09:00:00Z", position: 0, outcomes: [], records: { reset_notes: ["2 RFQ(s) of the last round cancelled"] } };
    return jsonResponse(200, view);
  }
  if (url.pathname === "/api/demo/next") {
    const body = await request.json();
    posted.push({ path: url.pathname, body });
    view = {
      ...view,
      position: 1,
      next: STEPS[1],
      outcomes: [{ key: "late_order_eta", status: "done", summary: "request_eta awaiting_approval; approved by Ana", links: [{ label: "approval #201", path: "/approvals?id=201" }, { label: "P00077", path: "/board?po=P00077" }], approval_ids: [201], at: "2026-09-14T09:01:00Z" }],
    };
    return jsonResponse(200, view);
  }
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
}

describe("demo mode", () => {
  beforeEach(() => {
    posted = [];
    view = {
      steps: STEPS,
      position: 0,
      started_at: null,
      finished: false,
      next: STEPS[0],
      outcomes: [],
      records: {},
      ready: { mailbox: false, world: true, notes: ["SC__DEMO__SMTP_USER / SMTP_PASSWORD not set: the supplier's replies (steps 2, 4 and 5) cannot be sent"] },
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the script with what to say, resets, runs the next step and links its outcome", async () => {
    window.history.replaceState(null, "", "/demo");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "Demo mode" })).toBeInTheDocument();
    expect(screen.getByRole("note", { name: "Some steps cannot run on this machine" })).toHaveTextContent("SMTP_PASSWORD not set");
    const script = screen.getByRole("list", { name: "The script" });
    const items = within(script).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveAttribute("aria-current", "step");
    expect(items[0]).toHaveTextContent("The department noticed.");
    expect(items[0]).toHaveTextContent("Where to look: Approvals: the email.");
    expect(items[1]).toHaveTextContent("needs the supplier's mailbox");
    expect(screen.getByText("Not started: reset to begin")).toBeInTheDocument();
    // before a reset the next step is not offered
    expect(screen.getByRole("button", { name: "Next: step 1" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(await screen.findByRole("status")).toHaveTextContent("The demo is back at the start. 2 RFQ(s) of the last round cancelled");
    await userEvent.click(screen.getByRole("checkbox", { name: "Decide the approvals for me" }));
    await userEvent.click(screen.getByRole("button", { name: "Next: step 1" }));
    await waitFor(() => expect(posted.map((p) => p.path)).toEqual(["/api/demo/reset", "/api/demo/next"]));
    expect(posted[1]!.body).toEqual({ approve: true, step: null });
    expect(await screen.findByText("1 of 3 steps")).toBeInTheDocument();
    const first = within(screen.getByRole("list", { name: "The script" })).getAllByRole("listitem")[0]!;
    expect(within(first).getByText("Done")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "approval #201" })).toHaveAttribute("href", "/approvals?id=201");
    expect(within(first).getByRole("link", { name: "P00077" })).toHaveAttribute("href", "/board?po=P00077");
    expect(screen.getByRole("status")).toHaveTextContent("Done: request_eta awaiting_approval; approved by Ana");
    // the second step is now current and can be run by name too
    const second = within(screen.getByRole("list", { name: "The script" })).getAllByRole("listitem")[1]!;
    expect(second).toHaveAttribute("aria-current", "step");
    await userEvent.click(within(first).getByRole("button", { name: "Run step 1" }));
    await waitFor(() => expect(posted).toHaveLength(3));
    expect(posted[2]!.body).toEqual({ approve: true, step: "late_order_eta" });
  });
});
