import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { Schemas } from "@/api/client";

const BOARD: Schemas["ExceptionsBoard"] = {
  as_of: "2026-09-14",
  late_pos: [
    {
      kind: "late_po",
      title: "P00011",
      detail: "2 days past 2026-09-12 without a receipt",
      po_id: 2,
      po_name: "P00011",
      partner_id: 7,
      case_id: "case_x",
      case_code: "C00007",
      days: 2,
      next_action: "request_eta (po_late)",
      next_action_at: "2026-09-13",
      can_act: true,
      odoo_url: "http://odoo/purchase.order/2",
    },
    { kind: "late_po", title: "P00012", detail: "9 days past", po_name: "P00012", days: 9, can_act: false },
  ],
  rfqs_no_reply: [],
  unlinked_mails: [{ kind: "unlinked_mail", title: "email without an order", detail: "C00009 (open)", case_id: "case_u", case_code: "C00009", days: 1, can_act: false }],
  failed_runs: [],
  stale_approvals: [{ kind: "stale_approval", title: "Send reminder", detail: "send_email pending for 3 days", approval_id: 7, days: 3, next_action: "expires", can_act: false }],
};

let acted: string[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/exceptions") return jsonResponse(200, BOARD);
  const act = url.pathname.match(/^\/api\/exceptions\/(\w+)\/(\w+)\/act$/);
  if (act && request.method === "POST") {
    acted.push(`${act[1]}:${act[2]}`);
    return jsonResponse(202, { accepted: true, po_name: act[2], message: "follow-up started" });
  }
  return jsonResponse(404, { detail: "no" });
}

describe("exceptions board", () => {
  beforeEach(() => {
    acted = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
    window.history.replaceState(null, "", "/exceptions");
  });
  afterEach(() => vi.restoreAllMocks());

  it("groups the columns, shows the next step and acts now", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    const late = await screen.findByRole("region", { name: "Late orders" });
    expect(within(late).getByText("P00011")).toBeInTheDocument();
    expect(late).toHaveTextContent("Next: request_eta (po_late)");
    expect(within(late).getAllByRole("button", { name: "Act now" })).toHaveLength(1); // P00012 waits for a person
    expect(within(screen.getByRole("region", { name: "Stale approvals" })).getByRole("link", { name: "Approval" })).toHaveAttribute("href", "/?id=7");
    expect(within(screen.getByRole("region", { name: "Unlinked emails" })).getByRole("link", { name: "C00009" })).toHaveAttribute("href", "/cases/C00009");
    expect(screen.getByRole("region", { name: "Failed runs" })).toHaveTextContent("Nothing here.");

    await userEvent.click(within(late).getByRole("button", { name: "Act now" }));
    await waitFor(() => expect(acted).toEqual(["late_po:P00011"]));
    expect(await within(late).findByRole("status")).toHaveTextContent("Follow-up started");
  });
});
