import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import { sanitizeEmailHtml } from "./EmailPreview";
import type { Approval } from "./types";

const EMAIL_HTML =
  '<p>Dear supplier,</p><img src="https://tracker.example/pixel.gif"><script>alert(1)</script><p>Please confirm the <a href="https://x">date</a>.</p>';

function approval(id: number, kind: string, payload: Record<string, unknown>, extra: Partial<Approval> = {}): Approval {
  return {
    id,
    kind,
    status: "pending",
    summary: `${kind} approval ${id}`,
    po_name: "P00015",
    requested_by: "supplier_comms",
    created_at: "2026-09-10T10:00:00Z",
    payload,
    why: id === 1 ? "rfq_silent: no reply for 3 days" : null,
    links: { odoo: "http://odoo/1", outlook: id === 1 ? "https://outlook/draft" : null },
    ...extra,
  } as Approval;
}

const ROWS: Approval[] = [
  approval(1, "send_email", { to: ["v@x.com"], subject: "[P00015] Follow-up", html_body: EMAIL_HTML, attachments: [] }),
  approval(2, "po_change", {
    summary: "delivery date 2026-10-20 on 2 lines",
    needs_review: 1,
    changes: [
      { po_line_id: 31, product: "Pump", field: "date_planned", before: "2026-10-01", after: "2026-10-20", source: "supplier", confidence: 0.95, needs_review: false },
      { po_line_id: 32, product: "Valve", field: "date_planned", before: "2026-10-01", after: "2026-10-20", source: "supplier", confidence: 0.95, needs_review: false },
      { po_line_id: 33, product: "Hose", field: "price", before: 10, after: 12, source: "supplier", confidence: 0.4, needs_review: true, review_reason: "currency mismatch" },
    ],
  }),
  approval(3, "planning_run", {
    run_id: "run_1",
    as_of: "2026-09-12",
    summary: "14 rules, 3 RFQs",
    totals: { lines: 14, rfq_lines: 3, rules_changed: 14, exceptions: 2 },
    exceptions: [{ line_id: "run_1:101", product_ref: "HYD-001", exception: "stockout_risk", action: "create_rfq", order_qty: 23 }],
    lines: [{ line_id: "run_1:101", product_ref: "HYD-001", action: "create_rfq", order_qty: 23 }, { line_id: "run_1:102", product_ref: "HYD-002", action: "update_rule" }],
  }),
];

let pending: Approval[];
let resolved: { id: number; body: Record<string, unknown> }[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/approvals") return jsonResponse(200, url.searchParams.get("status") === "pending" ? pending : ROWS);
  const one = url.pathname.match(/^\/api\/approvals\/(\d+)$/);
  if (one) {
    const row = ROWS.find((r) => r.id === Number(one[1]));
    return row ? jsonResponse(200, row) : jsonResponse(404, { detail: "nope" });
  }
  const resolve = url.pathname.match(/^\/api\/approvals\/(\d+)\/resolve$/);
  if (resolve && request.method === "POST") {
    const id = Number(resolve[1]);
    const body = (await request.json()) as Record<string, unknown>;
    resolved.push({ id, body });
    pending = pending.filter((r) => r.id !== id);
    return jsonResponse(200, { id, status: body.status, resolved_by: "Ana", callback_status: "sent" });
  }
  return jsonResponse(404, { detail: `unhandled ${request.method} ${url.pathname}` });
}

function renderInbox() {
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

describe("sanitizeEmailHtml", () => {
  it("drops scripts and images and opens links in a new tab", () => {
    const clean = sanitizeEmailHtml(EMAIL_HTML);
    expect(clean).not.toContain("<img");
    expect(clean).not.toContain("<script");
    expect(clean).toContain('target="_blank"');
    expect(clean).toContain("Dear supplier");
  });
});

describe("approvals inbox", () => {
  beforeEach(() => {
    pending = [...ROWS];
    resolved = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
    window.history.replaceState(null, "", "/");
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("lists pending approvals, previews the email safely and approves with an edited subject", async () => {
    renderInbox();
    const list = await screen.findByRole("list");
    await waitFor(() => expect(within(list).getAllByRole("button")).toHaveLength(3));
    // the first row is selected on a wide screen: its email preview shows, sanitised
    const preview = await screen.findByTestId("email-preview");
    expect(preview.innerHTML).not.toContain("<img");
    expect(preview.innerHTML).toContain("Dear supplier");
    expect(screen.getByText("rfq_silent: no reply for 3 days")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Draft in Outlook" })).toHaveAttribute("href", "https://outlook/draft");

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const subject = screen.getByLabelText("Subject");
    await userEvent.clear(subject);
    await userEvent.type(subject, "Follow-up on the pumps");
    await userEvent.click(screen.getByRole("button", { name: "Approve with edits" }));

    await waitFor(() => expect(resolved).toHaveLength(1));
    expect(resolved[0]).toEqual({
      id: 1,
      body: { status: "approved", reason: null, edited_payload: { subject: "Follow-up on the pumps" } },
    });
    await waitFor(() => expect(within(screen.getByRole("list")).getAllByRole("button")).toHaveLength(2));
  });

  it("approves only the ticked order lines", async () => {
    window.history.replaceState(null, "", "/?id=2");
    renderInbox();
    const boxes = await screen.findAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    expect(boxes[2]).toBeDisabled(); // needs a person's review
    expect(screen.getByText("currency mismatch")).toBeInTheDocument();
    await userEvent.click(boxes[1]!); // untick the valve
    await userEvent.click(screen.getByRole("button", { name: "Approve 1 line(s)" }));
    await waitFor(() => expect(resolved).toHaveLength(1));
    expect(resolved[0]?.body).toEqual({ status: "approved", reason: null, edited_payload: { accepted_line_ids: [31] } });
  });

  it("rejects with a reason and links a plan to its review", async () => {
    window.history.replaceState(null, "", "/?id=3");
    renderInbox();
    expect(await screen.findByRole("link", { name: /Review the plan/ })).toHaveAttribute("href", "/planning/run_1");
    expect(screen.getByText("2 exceptions")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await userEvent.type(screen.getByLabelText(/Why\?/), "wait for the month end");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(resolved).toHaveLength(1));
    expect(resolved[0]?.body).toEqual({ status: "rejected", reason: "wait for the month end", edited_payload: null });
  });

  it("hides the actions from a viewer", async () => {
    authStore.set({ token: "jwt", user: { email: "vic@x.com", name: "Vic", role: "viewer" } });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(String(input), init);
      if (new URL(request.url).pathname === "/api/auth/me")
        return jsonResponse(200, { email: "vic@x.com", name: "Vic", role: "viewer" });
      return fakeFetch(request);
    });
    renderInbox();
    await screen.findByTestId("email-preview");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});
