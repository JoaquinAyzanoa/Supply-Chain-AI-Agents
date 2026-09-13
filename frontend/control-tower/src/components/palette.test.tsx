import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import { diffLines, htmlToLines } from "@/features/approvals/DiffView";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/board") return jsonResponse(200, { as_of: "2026-09-14", due_soon_days: 5, cards: [{ po_id: 77, po_name: "P00077", partner_id: 8, partner_name: "Proveedor Hidraulica", state: "purchase", column: "incoming", delivery: "late", days_late: 7, amount_total: 2314, currency: "USD", odoo_url: "http://odoo/77", supplier_confirmed: false, discrepancy: false, escalated: false, can_act: false, age_days: 20 }], counts: {}, planning: null });
  if (url.pathname === "/api/performance/scores") return jsonResponse(200, [{ partner_id: 8, partner_name: "Proveedor Hidraulica", score: 75.5, otif: 0.5, period_start: "2026-09-01", period_end: "2026-09-07", samples: {} }]);
  if (url.pathname === "/api/assistant") return jsonResponse(200, []);
  if (url.pathname === "/api/home") return jsonResponse(200, { as_of: "2026-09-14", kpis: [], needs_you: [], pending: 0, late: 0, silent: 0, automated_week: 0 });
  if (url.pathname === "/api/briefing") return jsonResponse(404, { detail: "none" });
  if (url.pathname === "/api/autonomy/actions") return jsonResponse(200, []);
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
}

describe("command palette", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("opens with Ctrl+K, finds orders and suppliers, and hands a question to the assistant", async () => {
    window.history.replaceState(null, "", "/");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: "Command palette" });
    const input = within(dialog).getByLabelText("Search");
    await userEvent.type(input, "hidra");
    await waitFor(() => expect(within(dialog).getByText(/P00077 · Proveedor Hidraulica/)).toBeInTheDocument());
    expect(within(dialog).getByText("Ask your AI: hidra")).toBeInTheDocument();
    await userEvent.clear(input);
    await userEvent.type(input, "which orders are late?");
    await userEvent.click(within(dialog).getByText("Ask your AI: which orders are late?"));
    expect(await screen.findByRole("heading", { name: "Ask your AI" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message to your AI")).toHaveValue("which orders are late?");
  });

  it("diffs an edited email line by line", () => {
    const before = htmlToLines("<p>Dear supplier,</p><p>Please confirm the date.</p><p>Regards</p>");
    const after = htmlToLines("<p>Dear supplier,</p><p>Please confirm the date today.</p><p>Regards</p>");
    expect(diffLines(before, after).map((l) => `${l.kind}:${l.text}`)).toEqual(["same:Dear supplier,", "removed:Please confirm the date.", "added:Please confirm the date today.", "same:Regards"]);
  });
});
