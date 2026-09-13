import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";

const RULE = {
  id: "trusted",
  kind: "send_email",
  level: "auto_notice",
  revert_hours: 24,
  note: "Hidraulica reads what we send",
  enabled: true,
  when: { partner_ids: [8], email_kinds: ["request_eta"], amount_max: null, supplier_score_min: null, confidence_min: null, change_pct_max: null, change_days_max: null, days_late_max: null, first_time_supplier: null },
};

let me = { email: "adm@x.com", name: "Adm", role: "admin" };
let saved: Record<string, unknown>[];
let previews: Record<string, unknown>[];
let reverted: number[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, me);
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  if (url.pathname === "/api/settings/history") return jsonResponse(200, []);
  if (url.pathname === "/api/autonomy" && request.method === "GET")
    return jsonResponse(200, { version: 1, changed_by: "adm@x.com", changed_at: "2026-09-14T08:00:00Z", policy: { rules: [RULE] }, pending: null });
  if (url.pathname === "/api/autonomy" && request.method === "PUT") {
    const body = (await request.json()) as Record<string, unknown>;
    saved.push(body);
    return jsonResponse(200, { saved: null, approval_id: 31, widened: ["trusted", "rule-2"] });
  }
  if (url.pathname === "/api/autonomy/preview") {
    const body = (await request.json()) as Record<string, unknown>;
    previews.push(body);
    return jsonResponse(200, {
      days: 30,
      total: 12,
      would_run_alone: 5,
      by_rule: { trusted: 5 },
      by_kind: { send_email: { auto_notice: 5, approve: 7 } },
      rows: [{ approval_id: 7, kind: "send_email", summary: "Send date request to Proveedor Hidraulica for P00074", created_at: "2026-09-10T09:00:00Z", status: "approved", level: "auto_notice", rule_id: "trusted" }],
    });
  }
  if (url.pathname === "/api/autonomy/actions")
    return jsonResponse(200, [
      {
        id: 1,
        created_at: "2026-09-14T07:00:00Z",
        agent: "supplier_comms",
        kind: "po_change",
        level: "auto_notice",
        rule_id: "trusted-dates",
        summary: "Moved 2 lines of P00074 by 3 days",
        po_id: 74,
        po_name: "P00074",
        payload: {},
        revertible: true,
        revert_until: "2026-09-15T07:00:00Z",
        reverted_at: null,
        reverted_by: null,
      },
      { id: 2, created_at: "2026-09-14T06:00:00Z", agent: "supplier_comms", kind: "send_email", level: "auto", rule_id: "trusted", summary: "Sent a date request to Proveedor Hidraulica for P00077", po_name: "P00077", payload: {}, revertible: false, revert_until: null, reverted_at: null, reverted_by: null },
    ]);
  if (url.pathname === "/api/autonomy/actions/1/revert" && request.method === "POST") {
    reverted.push(1);
    return jsonResponse(200, { id: 1, reverted_at: "2026-09-14T08:30:00Z", note: "Automatic change #1 reverted by Adm" });
  }
  return jsonResponse(404, { detail: `unhandled ${request.method} ${url.pathname}` });
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

describe("autonomy page", () => {
  beforeEach(() => {
    saved = [];
    previews = [];
    reverted = [];
    me = { email: "adm@x.com", name: "Adm", role: "admin" };
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "adm@x.com", name: "Adm", role: "admin" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the rules, previews a draft and asks a second person when it widens", async () => {
    renderAt("/autonomy");
    expect(await screen.findByRole("heading", { name: "Autonomy" })).toBeInTheDocument();
    const rule = screen.getByTestId("rule-0");
    expect(within(rule).getByLabelText("Rule id")).toHaveValue("trusted");
    expect(within(rule).getByLabelText("Level")).toHaveValue("auto_notice");
    expect(within(rule).getByLabelText("Date requests")).toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const added = screen.getByTestId("rule-1");
    await userEvent.clear(within(added).getByLabelText("Rule id"));
    await userEvent.type(within(added).getByLabelText("Rule id"), "small-bills");
    await userEvent.selectOptions(within(added).getByLabelText("Kind"), "vendor_bill");
    await userEvent.type(within(added).getByLabelText("Amount ≤"), "500");

    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(previews).toHaveLength(1));
    const sent = previews[0] as { policy: { rules: { id: string; kind: string; when: { amount_max: number | null } }[] }; days: number };
    expect(sent.days).toBe(30);
    expect(sent.policy.rules.map((r) => r.id)).toEqual(["trusted", "small-bills"]);
    expect(sent.policy.rules[1]!.kind).toBe("vendor_bill");
    expect(sent.policy.rules[1]!.when.amount_max).toBe(500);
    expect(await screen.findByTestId("preview")).toHaveTextContent("5 of the 12 approvals of the last 30 days would have run alone");

    await userEvent.type(screen.getByLabelText("Note for the history"), "trust small bills");
    await userEvent.click(screen.getByRole("button", { name: "Save the policy" }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0]).toMatchObject({ note: "trust small bills" });
    expect(await screen.findByRole("status")).toHaveTextContent("Approval #31 created: another person must confirm the wider rules (trusted, rule-2).");
  });

  it("lists what ran alone and reverts an action inside its window", async () => {
    renderAt("/autonomy");
    const feed = await screen.findByTestId("auto-actions");
    expect(feed).toHaveTextContent("Moved 2 lines of P00074 by 3 days");
    expect(feed).toHaveTextContent("An email cannot be unsent.");
    expect(within(feed).getAllByRole("button", { name: "Revert" })).toHaveLength(1);
    await userEvent.click(within(feed).getByRole("button", { name: "Revert" }));
    await waitFor(() => expect(reverted).toEqual([1]));
    expect(await within(feed).findByRole("status")).toHaveTextContent("reverted by Adm");
  });

  it("is read-only for a viewer", async () => {
    me = { email: "vic@x.com", name: "Vic", role: "viewer" };
    authStore.set({ token: "jwt", user: { email: "vic@x.com", name: "Vic", role: "viewer" } });
    renderAt("/autonomy");
    expect(await screen.findByRole("heading", { name: "Autonomy" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add rule" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save the policy" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeInTheDocument();
    expect(within(screen.getByTestId("rule-0")).getByLabelText("Rule id")).toBeDisabled();
  });
});
