import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { PlanningRun, PlanningRunDetail, ReplenishmentLine } from "./api";

function line(id: string, over: Partial<ReplenishmentLine> = {}): ReplenishmentLine {
  return {
    line_id: id,
    product_id: Number(id.split(":")[1]),
    product_ref: `HYD-${id.split(":")[1]}`,
    product_name: "Pump",
    warehouse_id: 1,
    on_hand: 10,
    reserved: 0,
    incoming: 0,
    position: 10,
    forecast_daily: 2,
    forecast_method: "ses",
    sigma_daily: 0.5,
    wape: 0.12,
    history_periods: 52,
    lead_time_days: 7,
    sigma_lead_time_days: 1.75,
    service_level: 0.95,
    review_period_days: 7,
    abc_class: "A",
    ss: 5,
    rop: 19,
    order_up_to: 33,
    coverage_days: 5,
    proposed_min: 19,
    proposed_max: 33,
    order_qty: 23,
    supplier_id: 7,
    supplier_name: "Proveedor Hidraulica",
    unit_price: 10,
    currency: "PEN",
    moq: 0,
    action: "create_rfq",
    ...over,
  };
}

const RUN: PlanningRun = {
  run_id: "run_1",
  case_id: "plan_2026-09-14",
  kind: "daily_plan",
  as_of: "2026-09-14",
  warehouse_id: 1,
  status: "awaiting_approval",
  approval_id: 12,
  summary: "14 rules, 3 RFQs",
  totals: { lines: 3, rfq_lines: 2, exceptions: 1 },
  created_at: "2026-09-14T06:00:00Z",
  updated_at: "2026-09-14T06:00:00Z",
};

const DETAIL: PlanningRunDetail = {
  run: RUN,
  lines: [
    { line: line("run_1:101") },
    { line: line("run_1:102", { supplier_name: "Otro SAC", action: "update_rule", order_qty: 0, exception: "stockout_risk", explanation: "Demand doubled." }) },
    { line: line("run_1:103", { action: "none", supplier_name: "Otro SAC" }) },
  ],
};

let resolved: Record<string, unknown>[];
let simulated: Record<string, unknown>[];
let calendar: Record<string, unknown>[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  url.pathname = decodeURIComponent(url.pathname); // openapi-fetch encodes the ":" in line ids
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/planning/runs") return jsonResponse(200, [RUN]);
  if (url.pathname === "/api/planning/calendar" && request.method === "GET") return jsonResponse(200, calendar);
  if (url.pathname === "/api/planning/calendar" && request.method === "POST") {
    const body = (await request.json()) as Record<string, unknown>;
    calendar.push({ ...body, id: calendar.length + 1, created_by: "ana@x.com" });
    return jsonResponse(201, calendar[calendar.length - 1]);
  }
  if (url.pathname.startsWith("/api/planning/calendar/") && request.method === "DELETE") {
    calendar = calendar.filter((e) => e.id !== Number(url.pathname.split("/").pop()));
    return new Response(null, { status: 204 });
  }
  if (url.pathname === "/api/planning/runs/run_1/what-if-class" && request.method === "POST") {
    const body = (await request.json()) as Record<string, unknown>;
    simulated.push(body);
    return jsonResponse(200, {
      abc_class: body.abc_class,
      run_id: "run_3",
      baseline: { lines: 1, orders: 1, spend: 230, stock_value: 330, expected_stockouts: 0.9, service_level: 0.95 },
      simulated: { lines: 1, orders: 1, spend: 270, stock_value: 370, expected_stockouts: 0.4, service_level: 0.99 },
    });
  }
  if (url.pathname === "/api/planning/runs/run_1") return jsonResponse(200, DETAIL);
  if (url.pathname === "/api/planning/runs/run_1/ranking")
    return jsonResponse(200, {
      run_id: "run_1",
      better_count: 1,
      lines: [
        {
          line_id: "run_1:102",
          product_id: 102,
          supplier_id: 7,
          better: { rank: 1, partner_id: 46, partner_name: "Hidraulica Alterna", score: 91.2, otif: 0.95, lead_time_mean_days: 9, price: 98, currency: "PEN", min_qty: 0, promised_lead_days: 7, samples: {}, why: "score 91.2" },
          suppliers: [
            { rank: 1, partner_id: 46, partner_name: "Hidraulica Alterna", score: 91.2, otif: 0.95, lead_time_mean_days: 9, price: 98, currency: "PEN", min_qty: 0, promised_lead_days: 7, samples: {}, why: "score 91.2" },
            { rank: 2, partner_id: 7, partner_name: "Proveedor Hidraulica", score: 75.5, otif: 0.5, lead_time_mean_days: 42.3, price: 104.16, currency: "PEN", min_qty: 0, promised_lead_days: 10, samples: {}, why: "score 75.5" },
            { rank: 3, partner_id: 47, partner_name: "Nuevo Proveedor", score: null, otif: null, lead_time_mean_days: null, price: 90, currency: "PEN", min_qty: 0, promised_lead_days: 5, samples: {}, why: "no history yet" },
          ],
        },
      ],
    });
  if (url.pathname === "/api/planning/runs/run_1/lines/run_1:102/demand")
    return jsonResponse(200, {
      line_id: "run_1:102",
      product_id: 102,
      forecast_daily: 2,
      sigma_daily: 0.5,
      days: [
        { day: "2026-09-12", ordered: 3, delivered: 3 },
        { day: "2026-09-13", ordered: 0, delivered: 0 },
      ],
    });
  if (url.pathname === "/api/planning/runs/run_1/what-if" && request.method === "POST") {
    const body = (await request.json()) as Record<string, unknown>;
    simulated.push(body);
    return jsonResponse(200, {
      run_id: "run_2",
      baseline: DETAIL.lines[1]!.line,
      simulated: line("run_2:102", { service_level: 0.99, ss: 9, rop: 23, order_up_to: 37, order_qty: 27, explanation: "Higher service level." }),
    });
  }
  if (url.pathname === "/api/approvals/12/resolve" && request.method === "POST") {
    resolved.push((await request.json()) as Record<string, unknown>);
    return jsonResponse(200, { id: 12, status: "approved", resolved_by: "Ana", callback_status: "sent" });
  }
  return jsonResponse(404, { detail: `unhandled ${url.pathname}` });
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

describe("planning review", () => {
  beforeEach(() => {
    resolved = [];
    simulated = [];
    calendar = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("lists runs with their totals", async () => {
    renderAt("/planning");
    expect(await screen.findByRole("link", { name: "Sep 14, 2026" })).toHaveAttribute("href", "/planning/run_1");
    expect(screen.getByText("14 rules, 3 RFQs")).toBeInTheDocument();
  });

  it("keeps the demand calendar: an approver adds a promotion and removes it", async () => {
    renderAt("/planning");
    const section = await screen.findByRole("region", { name: "Demand calendar" });
    expect(await within(section).findByText("No events yet.")).toBeInTheDocument();
    await userEvent.type(within(section).getByLabelText("Name"), "Feria");
    await userEvent.type(within(section).getByLabelText("From"), "2026-10-01");
    await userEvent.type(within(section).getByLabelText("To"), "2026-10-07");
    await userEvent.clear(within(section).getByLabelText("Demand factor"));
    await userEvent.type(within(section).getByLabelText("Demand factor"), "2");
    await userEvent.click(within(section).getByRole("button", { name: "Add event" }));
    await waitFor(() => expect(calendar).toHaveLength(1));
    expect(calendar[0]).toMatchObject({ kind: "promotion", name: "Feria", start_date: "2026-10-01", end_date: "2026-10-07", factor: 2, quantity: 0, product_id: null });
    expect(await within(section).findByText("Feria added; the next plan uses it.")).toBeInTheDocument();
    const row = (await within(section).findAllByRole("row"))[1]!;
    expect(row).toHaveTextContent("Promotion");
    expect(row).toHaveTextContent("all products");
    expect(row).toHaveTextContent("×2");
    await userEvent.click(within(section).getByRole("button", { name: "Remove Feria" }));
    await waitFor(() => expect(calendar).toHaveLength(0));
    expect(await within(section).findByText("No events yet.")).toBeInTheDocument();
  });

  it("simulates a whole class and shows the totals before and after", async () => {
    renderAt("/planning/run_1");
    const panel = await screen.findByRole("region", { name: "Portfolio what-if" });
    await userEvent.selectOptions(within(panel).getByLabelText("Class"), "B");
    await userEvent.clear(within(panel).getByLabelText("Service level"));
    await userEvent.type(within(panel).getByLabelText("Service level"), "0.99");
    await userEvent.click(within(panel).getByRole("button", { name: "Simulate class" }));
    await waitFor(() => expect(simulated).toHaveLength(1));
    expect(simulated[0]).toEqual({ abc_class: "B", overrides: { service_level: 0.99, review_period_days: null, lead_time_days: null, max_coverage_days: null } });
    const rows = await within(panel).findAllByRole("row");
    expect(rows[2]).toHaveTextContent("Spend");
    expect(rows[2]).toHaveTextContent("230");
    expect(rows[2]).toHaveTextContent("270");
    expect(rows[4]).toHaveTextContent("Expected stockouts (30 d)");
    expect(rows[4]).toHaveTextContent("0.4");
  });

  it("groups lines by supplier, edits a quantity and approves only the selected lines", async () => {
    renderAt("/planning/run_1");
    expect(await screen.findByRole("heading", { name: /Proveedor Hidraulica/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Otro SAC/ })).toBeInTheDocument();
    expect(screen.getByLabelText("HYD-101")).toBeChecked();
    expect(screen.getByLabelText("HYD-102")).toBeChecked();
    expect(screen.getByLabelText("HYD-103")).not.toBeChecked();
    expect(screen.getByLabelText("HYD-103")).toBeDisabled(); // action none: nothing to approve

    const qty = screen.getByLabelText("HYD-101 qty");
    await userEvent.clear(qty);
    await userEvent.type(qty, "30");
    expect(screen.getByText("1 edited")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("HYD-102")); // untick the rule update

    await userEvent.click(screen.getByRole("button", { name: "Approve 1 line(s)" }));
    await waitFor(() => expect(resolved).toHaveLength(1));
    expect(resolved[0]).toEqual({
      status: "approved",
      edited_payload: { accepted_line_ids: ["run_1:101"], edits: { "run_1:101": { order_qty: 30 } } },
    });
    expect(await screen.findByRole("status")).toHaveTextContent("1 line(s) approved");
  });

  it("opens the drawer with the explanation, the sparkline and a what-if", async () => {
    renderAt("/planning/run_1");
    await userEvent.click(await screen.findByRole("button", { name: "HYD-102" }));
    const drawer = screen.getByRole("complementary", { name: "Line details" });
    expect(drawer).toHaveTextContent("Demand doubled.");
    // the ranking: the chosen supplier is marked, a better one is called out, a newcomer is listed
    expect(await screen.findByText("Better rated: Hidraulica Alterna (91)")).toBeInTheDocument();
    const rankTable = within(drawer).getByRole("table", { name: "Suppliers for this product" });
    const rankRows = within(rankTable).getAllByRole("row");
    expect(rankRows[1]).toHaveTextContent("Hidraulica Alterna");
    expect(rankRows[2]).toHaveTextContent("Proveedor Hidraulica(reference)");
    expect(rankRows[3]).toHaveTextContent("no history");
    expect(drawer).toHaveTextContent("Hidraulica Alterna scores higher than the reference supplier.");
    expect(await within(drawer).findByRole("img", { name: "demand 2 days" })).toBeInTheDocument();
    expect(drawer).toHaveTextContent("Forecast ses: 2 per day (WAPE 12%)");

    await userEvent.type(within(drawer).getByLabelText("Service level"), "0.99");
    await userEvent.click(within(drawer).getByRole("button", { name: "Simulate" }));
    await waitFor(() => expect(simulated).toHaveLength(1));
    expect(simulated[0]).toEqual({ line_id: "run_1:102", overrides: { service_level: 0.99 } });
    await waitFor(() => expect(drawer).toHaveTextContent("Higher service level."));
    expect(within(drawer).getByRole("columnheader", { name: "What-if" })).toBeInTheDocument();
    expect(drawer).toHaveTextContent("27");
  });
});
