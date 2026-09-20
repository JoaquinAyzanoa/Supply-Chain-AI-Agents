import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { Approval } from "./types";

function approval(id: number, summary: string, emailKind: string): Approval {
  return {
    id,
    kind: "send_email",
    status: "pending",
    summary,
    po_id: id,
    po_name: `P000${id}`,
    requested_by: "supplier_comms",
    case_id: `case_${id}`,
    case_code: null,
    thread_id: `t_${id}`,
    created_at: "2026-09-13T09:00:00Z",
    resolved_at: null,
    resolved_by: null,
    reason: null,
    payload: { to: ["v@x.com"], subject: `[P000${id}] x`, html_body: "<p>Hola</p>", attachments: [], facts: { email_kind: emailKind, partner_name: "Proveedor Hidraulica" } },
    why: null,
    reasoning: null,
    links: {},
    playbook: null,
  } as unknown as Approval;
}

let rows: Approval[];
let bulk: Record<string, unknown>[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/approvals" && request.method === "GET") return jsonResponse(200, rows);
  if (url.pathname.match(/^\/api\/approvals\/\d+$/)) return jsonResponse(200, rows.find((r) => r.id === Number(url.pathname.split("/").pop())));
  if (url.pathname === "/api/approvals/bulk") {
    const body = (await request.json()) as { ids: number[]; status: string };
    bulk.push(body);
    rows = rows.filter((r) => !body.ids.includes(r.id));
    return jsonResponse(200, { resolved: body.ids.length, results: body.ids.map((id) => ({ id, status: body.status, error: null })) });
  }
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
}

describe("bulk approvals", () => {
  beforeEach(() => {
    rows = [approval(1, "Reminder to Proveedor Hidraulica", "follow_up"), approval(2, "ETA request to Proveedor Hidraulica", "request_eta"), approval(3, "ETA request to Importadora", "request_eta")];
    bulk = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("filters by email kind, selects all shown and approves them in one call; j moves the selection", async () => {
    window.history.replaceState(null, "", "/approvals?email=request_eta");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: /ETA request to Importadora/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reminder to/ })).toBeNull(); // filtered out
    await userEvent.click(screen.getByRole("checkbox", { name: "Select all shown" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve 2" }));
    await waitFor(() => expect(bulk).toEqual([{ ids: [2, 3], status: "approved", reason: null }]));
    expect(await screen.findByRole("status")).toHaveTextContent("2 decided, 0 could not be");
    // the keyboard moves through what is left
    rows = [approval(4, "One more", "follow_up"), approval(5, "And another", "follow_up")];
    window.history.replaceState(null, "", "/approvals");
  });

  it("j and k walk the list", async () => {
    window.history.replaceState(null, "", "/approvals?id=1");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    const first = await screen.findByRole("button", { name: /Reminder to Proveedor/ });
    expect(first).toHaveAttribute("aria-current", "true");
    fireEvent.keyDown(window, { key: "j" });
    await waitFor(() => expect(screen.getByRole("button", { name: /ETA request to Proveedor/ })).toHaveAttribute("aria-current", "true"));
    fireEvent.keyDown(window, { key: "k" });
    await waitFor(() => expect(screen.getByRole("button", { name: /Reminder to Proveedor/ })).toHaveAttribute("aria-current", "true"));
  });
});
