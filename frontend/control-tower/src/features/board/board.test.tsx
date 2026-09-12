import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { Schemas } from "@/api/client";

type Card = Schemas["BoardCard"];

const card = (over: Partial<Card> & Pick<Card, "po_id" | "po_name" | "column" | "state">): Card => ({
  partner_id: 7,
  partner_name: "Proveedor Hidraulica",
  buyer: "Mitchell Admin",
  amount_total: 1200.5,
  currency: "USD",
  receipt_status: null,
  date_planned: null,
  eta_source: null,
  supplier_confirmed: false,
  delivery: "none",
  days_late: 0,
  days_silent: null,
  last_outbound: null,
  last_inbound: null,
  next_action: null,
  next_action_at: null,
  pending_approval: null,
  escalated: false,
  on_hold_until: null,
  case_id: null,
  case_code: null,
  case_status: null,
  summary: null,
  odoo_url: `http://odoo/purchase.order/${over.po_id}`,
  ...over,
});

const BOARD: Schemas["Board"] = {
  as_of: "2026-09-14",
  due_soon_days: 5,
  counts: { proposed: 0, rfq_sent: 1, quote_received: 1, confirmed: 0, incoming: 1, received: 0, closed: 1 },
  cards: [
    card({ po_id: 2, po_name: "P00002", column: "rfq_sent", state: "sent", days_silent: 3, last_outbound: "2026-09-11", next_action: "follow_up", next_action_at: "2026-09-14" }),
    card({ po_id: 3, po_name: "P00003", column: "quote_received", state: "sent", pending_approval: { id: 31, kind: "po_change", summary: "date change" } }),
    card({
      po_id: 6,
      po_name: "P00006",
      column: "incoming",
      state: "purchase",
      receipt_status: "pending",
      date_planned: "2026-09-10",
      eta_source: "supplier",
      supplier_confirmed: true,
      delivery: "late",
      days_late: 4,
      escalated: true,
      case_id: "case_x",
      case_code: "C00007",
      case_status: "escalated",
      summary: "4 days late, supplier silent",
    }),
    card({ po_id: 8, po_name: "P00008", column: "closed", state: "done" }),
  ],
};

let moves: { po: string; body: Record<string, unknown> }[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/board") return jsonResponse(200, BOARD);
  const move = url.pathname.match(/^\/api\/board\/(\w+)\/move$/);
  if (move && request.method === "POST") {
    const body = (await request.json()) as Record<string, unknown>;
    moves.push({ po: move[1]!, body });
    return jsonResponse(200, { po_name: move[1], column: body.to, message: `${move[1]} confirmed` });
  }
  if (url.pathname === "/api/cases/case_x")
    return jsonResponse(200, {
      case: { case_id: "case_x", code: "C00007", kind: "eta", po_name: "P00006", status: "escalated", created_at: "2026-09-10T08:00:00Z", updated_at: "2026-09-14T08:00:00Z" },
      events: [{ id: 1, at: "2026-09-14T08:00:00Z", kind: "escalated", payload: { summary: "Supplier silent for 4 days" } }],
      runs: [],
    });
  if (url.pathname === "/api/cases/C00007/chat") return jsonResponse(200, []);
  return jsonResponse(404, { detail: `no ${url.pathname}` });
}

function renderBoard() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider initial="en">
        <AuthProvider>
          <Routed />
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("orders board", () => {
  beforeEach(() => {
    moves = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
    window.history.replaceState(null, "", "/");
  });
  afterEach(() => vi.restoreAllMocks());

  it("puts every order in its column with what a buyer looks for first", async () => {
    renderBoard();
    const incoming = await screen.findByRole("region", { name: "To receive" });
    const late = within(incoming).getByRole("article", { name: "P00006" });
    expect(late).toHaveTextContent("4 d late");
    expect(late).toHaveTextContent("Needs a person");
    expect(late).toHaveTextContent("Supplier confirmed");
    const sent = within(screen.getByRole("region", { name: "Quotation requested" })).getByRole("article", { name: "P00002" });
    expect(sent).toHaveTextContent("3 d without an answer");
    expect(sent).toHaveTextContent("Next: Reminder");
    expect(within(screen.getByRole("region", { name: "Quotation received" })).getByRole("article", { name: "P00003" })).toHaveTextContent("Needs approval");
    expect(screen.getByRole("region", { name: "Proposals" })).toHaveTextContent("No orders here.");
    // cards with nothing to do cannot be dragged; the others expose a handle
    expect(within(late).queryByRole("button", { name: "Drag P00006" })).toBeNull();
    expect(within(sent).getByRole("button", { name: "Drag P00002" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "Only with problems" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "P00002" })).toBeNull());
    expect(screen.getByRole("article", { name: "P00006" })).toBeInTheDocument();
  });

  it("opens the order panel with its history and confirms an RFQ from there", async () => {
    renderBoard();
    await userEvent.click(await screen.findByRole("button", { name: "Open P00006" }));
    const drawer = await screen.findByRole("dialog", { name: "Order P00006" });
    expect(drawer).toHaveTextContent("4 days late, supplier silent");
    expect(within(drawer).getByRole("link", { name: "Open in Odoo" })).toHaveAttribute("href", "http://odoo/purchase.order/6");
    expect(within(drawer).getByRole("link", { name: "Case C00007" })).toHaveAttribute("href", "/cases/C00007");
    expect(await within(drawer).findByText("Supplier silent for 4 days")).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Unmark supplier confirmation" })).toBeInTheDocument();
    await userEvent.click(within(drawer).getByRole("button", { name: "Close panel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEvent.click(screen.getByRole("button", { name: "Open P00002" }));
    const rfq = await screen.findByRole("dialog", { name: "Order P00002" });
    expect(rfq).toHaveTextContent("The agents have not worked on this order yet");
    await userEvent.click(within(rfq).getByRole("button", { name: "Confirm the order" }));
    await waitFor(() => expect(moves).toEqual([{ po: "P00002", body: { to: "confirmed", note: null } }]));
    expect(await within(rfq).findByRole("status")).toHaveTextContent("P00002 confirmed");
  });
});
