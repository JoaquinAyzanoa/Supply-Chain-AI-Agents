import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import { AwardCard, OfferCard, PartnerCard } from "@/features/approvals/SourcingCards";
import { awardPayload, kindOf, offerPayload, partnerPayload } from "@/features/approvals/types";

const AWARD = awardPayload.parse({
  round_id: 1,
  mode: "round",
  source_po_name: "P00081",
  recommended_partner_id: 8,
  comparison: {
    round_id: 1,
    basket: [{ product_id: 1, product: "[CBEA-LHN] Válvula", qty: 10 }],
    invited: 3,
    replied: 2,
    freight_pct: 5,
    recommendation: "Proveedor Hidraulica: lowest landed total; 28 day(s) lead time; score 82/100",
    recommended_partner_id: 8,
    line_awards: [{ product_id: 1, product: "[CBEA-LHN] Válvula", partner_id: 8, partner_name: "Proveedor Hidraulica", po_name: "P00081", landed_unit: 107.1, reasons: ["cheapest landed unit 107.10"] }],
    quotes: [
      { partner_id: 8, partner_name: "Proveedor Hidraulica", po_name: "P00081", source: "reply", currency: "USD", total: 1071, lead_days: 28, score: 82, rank: 1, recommended: true, reasons: ["lowest landed total", "score 82/100"], lines: [{ product_id: 1, product: "Válvula", qty: 10, price_unit: 102, landed_unit: 107.1 }] },
      { partner_id: 9, partner_name: "Hidráulica Alterna SAC", po_name: "P00901", source: "reply", currency: "USD", total: 1176, lead_days: 18, score: 70, rank: 2, reasons: ["9.8% above the lowest", "fastest: 18 day(s)"], lines: [{ product_id: 1, product: "Valvula", qty: 10, price_unit: 112, landed_unit: 117.6 }] },
      { partner_id: 10, partner_name: "Importadora del Sur SAC", po_name: "P00902", source: "none", total: null, rank: 3, reasons: [], lines: [] },
    ],
  },
});

const OFFER = offerPayload.parse({
  po_name: "P00081",
  partner_id: 8,
  partner_name: "Proveedor Hidraulica",
  product_id: 1,
  product: "[CBEA-LHN] Válvula",
  qty: 10,
  currency: "USD",
  current_price: 110,
  target_price: 104.16,
  floor_price: 99,
  offered_price: 104.16,
  cap_pct: 10,
  round_no: 1,
  max_rounds: 2,
  basis: "our last paid price of 104.16",
  justification: "We ask our last price; delivery terms unchanged.",
});

describe("sourcing approval cards", () => {
  it("shows the comparison, marks the recommendation and lets the buyer pick another winner", async () => {
    const user = userEvent.setup();
    const onChoice = vi.fn();
    render(
      <I18nProvider initial="en">
        <AwardCard payload={AWARD} choice={{ "1": 8 }} onChoice={onChoice} canAct />
      </I18nProvider>,
    );
    expect(screen.getByText("Round #1")).toBeInTheDocument();
    expect(screen.getByText("2 of 3 invited suppliers replied")).toBeInTheDocument();
    expect(screen.getAllByText("[CBEA-LHN] Válvula").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Recommended")).toBeInTheDocument();
    const alterna = screen.getByRole("radio", { name: "Award to Hidráulica Alterna SAC" });
    await user.click(alterna);
    expect(onChoice).toHaveBeenCalledWith({ "1": 9 });
    // and per line: the valve can go to any supplier who priced it
    const select = screen.getByLabelText("Winner for [CBEA-LHN] Válvula");
    await user.selectOptions(select, "9");
    expect(onChoice).toHaveBeenLastCalledWith({ "1": 9 });
    expect(screen.getByText(/cheapest landed unit 107.10/)).toBeInTheDocument();
    // a supplier without a price cannot be awarded
    expect(screen.getByRole("radio", { name: "Award to Importadora del Sur SAC" })).toBeDisabled();
    expect(screen.getByText(/9.8% above the lowest/)).toBeInTheDocument();
    expect(kindOf({ kind: "award" } as never)).toBe("award");
  });

  it("keeps the counter-offer between the floor and the quoted price", async () => {
    const user = userEvent.setup();
    const onOffered = vi.fn();
    render(
      <I18nProvider initial="en">
        <OfferCard payload={OFFER} offered="104.16" onOffered={onOffered} canAct />
      </I18nProvider>,
    );
    expect(screen.getAllByText(/110[.,]00 USD/).length).toBeGreaterThan(0);
    expect(screen.getByText(/our last paid price of 104.16/)).toBeInTheDocument();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/Between 99[.,]00 USD and 110[.,]00 USD/)).toBeInTheDocument();
    const input = screen.getByLabelText("Price we ask (per unit)");
    await user.clear(input);
    await user.type(input, "9");
    expect(onOffered).toHaveBeenCalled();
  });

  it("lets the buyer fix the new supplier's name and shows the quoted lines", () => {
    const payload = partnerPayload.parse({
      sender_address: "ventas@aceros-lima.com",
      suggested_name: "Aceros Lima",
      currency: "USD",
      web_link: "https://outlook/new",
      lines: [{ product_ref: "CBEA-LHN", description: "Válvula", qty: 10, unit_price: 98.5, lead_days: 20 }],
    });
    render(
      <I18nProvider initial="en">
        <PartnerCard payload={payload} name="Aceros Lima" onName={() => undefined} canAct />
      </I18nProvider>,
    );
    expect(screen.getByText("ventas@aceros-lima.com")).toBeInTheDocument();
    expect(screen.getByLabelText("Company name")).toHaveValue("Aceros Lima");
    expect(screen.getByText("CBEA-LHN")).toBeInTheDocument();
    expect(screen.getByText("20 d")).toBeInTheDocument();
  });
});

// --- the rounds panel on the Suppliers page ---------------------------------------------

const ROUND = {
  id: 1,
  case_id: "round_abc",
  status: "open",
  source_po_name: "P00081",
  basket: [{ product_id: 1, product: "[CBEA-LHN] Válvula", qty: 10 }],
  deadline: "2026-09-19T09:00:00Z",
  rfqs: [
    { partner_id: 8, partner_name: "Proveedor Hidraulica", po_name: "P00081", status: "sent", replied_at: "2026-09-15T10:00:00Z" },
    { partner_id: 9, partner_name: "Hidráulica Alterna SAC", po_name: "P00901", status: "no_email" },
  ],
  created_at: "2026-09-14T09:00:00Z",
  updated_at: "2026-09-14T09:00:00Z",
};

let started: Record<string, unknown>[];
let compared: number[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
  if (url.pathname === "/api/performance/scores") return jsonResponse(200, []);
  if (url.pathname === "/api/sourcing/rounds" && request.method === "GET") return jsonResponse(200, [ROUND]);
  if (url.pathname === "/api/sourcing/rounds" && request.method === "POST") {
    started.push((await request.json()) as Record<string, unknown>);
    return jsonResponse(202, { case_id: "c1", thread_id: "round_x", status: "sent", summary: "quote round #2 started: 2 supplier(s) invited for [CBEA-LHN] Válvula" });
  }
  if (url.pathname === "/api/sourcing/rounds/1/compare") {
    compared.push(1);
    return jsonResponse(202, { case_id: "c1", thread_id: "round_abc_cmp_1", status: "awaiting_approval", summary: "waiting for human approval", approval_id: 41 });
  }
  return jsonResponse(404, { detail: `unhandled ${request.method} ${url.pathname}` });
}

describe("quote rounds on the suppliers page", () => {
  beforeEach(() => {
    started = [];
    compared = [];
    window.history.replaceState(null, "", "/suppliers");
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "tok", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("lists the rounds with their invitations and starts or compares one", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    const panel = await screen.findByRole("region", { name: "Quote rounds" });
    expect(await within(panel).findByText(/Proveedor Hidraulica · P00081 · sent · replied/)).toBeInTheDocument();
    expect(within(panel).getByText(/Hidráulica Alterna SAC · P00901 · no email in Odoo/)).toBeInTheDocument();
    expect(within(panel).getByText("open")).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Compare round #1 now" }));
    await waitFor(() => expect(compared).toEqual([1]));
    expect(await within(panel).findByRole("status")).toHaveTextContent("waiting for human approval");

    await user.type(within(panel).getByLabelText("Order or RFQ"), "p00082");
    await user.click(within(panel).getByRole("button", { name: "Start round" }));
    await waitFor(() => expect(started).toEqual([{ po_name: "P00082" }]));
    expect(await within(panel).findByRole("status")).toHaveTextContent("quote round #2 started");
  });
});
