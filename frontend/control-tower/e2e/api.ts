/**
 * A small in-memory director for the browser tests: every /api route the UI
 * calls, with the shapes the real API answers (see openapi.json). Tests read
 * what was resolved through `state`.
 */
import type { Page, Route } from "@playwright/test";

export type Role = "viewer" | "approver" | "admin";

const USERS: Record<string, { name: string; role: Role; password: string }> = {
  "ana@x.com": { name: "Ana", role: "approver", password: "s3cret!!" },
  "vic@x.com": { name: "Vic", role: "viewer", password: "s3cret!!" },
  "adm@x.com": { name: "Adm", role: "admin", password: "s3cret!!" },
};

const EMAIL_HTML = "<p>Dear supplier,</p><img src=\"https://tracker.example/pixel.gif\"><p>Please confirm the delivery date.</p>";

export function makeState() {
  const bulk: { ids: number[]; status: string }[] = [];
  const approvals = [
    {
      id: 1,
      kind: "send_email",
      status: "pending",
      summary: "Send follow-up to Proveedor Hidraulica for P00015",
      po_id: 15,
      po_name: "P00015",
      requested_by: "supplier_comms",
      case_id: "case_a",
      thread_id: "followup_p00015",
      created_at: "2026-09-12T09:00:00Z",
      payload: { to: ["ventas@proveedor.com"], subject: "[P00015] Follow-up", html_body: EMAIL_HTML, attachments: [] },
      why: "rfq_silent: no reply to the RFQ for 3 days",
      links: { odoo: "http://odoo/sc.approval/1", order: "http://odoo/purchase.order/15", outlook: "https://outlook/draft1", trace: null },
    },
    {
      id: 2,
      kind: "planning_run",
      status: "pending",
      summary: "Plan 2026-09-14: 2 RFQs, 3 rules, 1 exception",
      po_id: null,
      po_name: null,
      requested_by: "inventory_planning",
      case_id: "plan_1",
      thread_id: "plan_1",
      created_at: "2026-09-14T06:00:00Z",
      payload: {
        run_id: "run_1",
        as_of: "2026-09-14",
        summary: "2 RFQs, 3 rules",
        totals: { lines: 3, rfq_lines: 2, rules_changed: 3, exceptions: 1 },
        exceptions: [],
        lines: [{ line_id: "run_1:101", product_ref: "HYD-101", action: "create_rfq", order_qty: 23 }],
      },
      why: null,
      links: { odoo: "http://odoo/sc.approval/2" },
    },
  ];
  const line = (id: number, over: Record<string, unknown> = {}) => ({
    line_id: `run_1:${id}`,
    product_id: id,
    product_ref: `HYD-${id}`,
    product_name: "Hydraulic pump",
    warehouse_id: 1,
    on_hand: 10,
    reserved: 0,
    incoming: 0,
    position: 10,
    forecast_daily: 2,
    forecast_method: "ses",
    sigma_daily: 0.5,
    wape: 0.1,
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
  });
  const run = {
    run_id: "run_1",
    case_id: "plan_1",
    kind: "daily_plan",
    as_of: "2026-09-14",
    warehouse_id: 1,
    status: "awaiting_approval",
    approval_id: 2,
    summary: "2 RFQs, 3 rules",
    totals: { lines: 3, rfq_lines: 2, exceptions: 1 },
    created_at: "2026-09-14T06:00:00Z",
    updated_at: "2026-09-14T06:00:00Z",
  };
  return {
    approvals,
    bulk,
    run,
    lines: [{ line: line(101) }, { line: line(102, { action: "update_rule", order_qty: 0, exception: "stockout_risk", explanation: "Demand doubled." }) }, { line: line(103, { action: "none" }) }],
    board: {
      as_of: "2026-09-14",
      due_soon_days: 5,
      planning: { approval_id: 2, run_id: "run_1", as_of: "2026-09-14", summary: "2 RFQs, 3 rules" },
      counts: { proposed: 0, rfq_sent: 1, quote_received: 0, confirmed: 0, incoming: 1, received: 0, invoicing: 0, closed: 0 },
      cards: [
        {
          po_id: 15,
          po_name: "P00015",
          partner_id: 7,
          partner_name: "Proveedor Hidraulica",
          buyer: "Ana",
          amount_total: 1200.5,
          currency: "PEN",
          state: "sent",
          receipt_status: null,
          date_planned: null,
          eta_source: null,
          supplier_confirmed: false,
          column: "rfq_sent",
          delivery: "none",
          days_late: 0,
          days_silent: 3,
          last_outbound: "2026-09-11",
          last_inbound: null,
          next_action: "follow_up",
          next_action_at: "2026-09-14",
          pending_approval: { id: 1, kind: "send_email", summary: "Send follow-up to Proveedor Hidraulica for P00015" },
          escalated: false,
          on_hold_until: null,
          case_id: "case_a",
          case_code: "C00001",
          case_status: "awaiting_approval",
          summary: "RFQ sent, waiting for the supplier",
          act_kind: "rfq_no_reply",
          can_act: false,
          invoice_status: null,
          discrepancy: false,
          odoo_url: "http://odoo/purchase.order/15",
        },
        {
          po_id: 16,
          po_name: "P00016",
          partner_id: 7,
          partner_name: "Proveedor Hidraulica",
          buyer: "Ana",
          amount_total: 800,
          currency: "PEN",
          state: "purchase",
          receipt_status: "pending",
          date_planned: "2026-09-12",
          eta_source: "supplier",
          supplier_confirmed: true,
          column: "incoming",
          delivery: "late",
          days_late: 2,
          days_silent: null,
          last_outbound: null,
          last_inbound: "2026-09-08",
          next_action: "request_eta",
          next_action_at: "2026-09-14",
          pending_approval: null,
          escalated: false,
          on_hold_until: null,
          case_id: null,
          case_code: null,
          case_status: null,
          summary: null,
          act_kind: "late_po",
          can_act: true,
          invoice_status: null,
          discrepancy: false,
          odoo_url: "http://odoo/purchase.order/16",
        },
      ],
    },
    moves: [] as { po: string; body: Record<string, unknown> }[],
    resolved: [] as { id: number; body: Record<string, unknown> }[],
    logins: [] as string[],
  };
}
export type ApiState = ReturnType<typeof makeState>;

export async function mockApi(page: Page, state: ApiState): Promise<void> {
  const json = (route: Route, status: number, body: unknown) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  const sessions = new Map<string, string>();

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = decodeURIComponent(url.pathname);
    const auth = request.headers()["authorization"] ?? "";
    const email = sessions.get(auth.replace("Bearer ", "")) ?? null;

    if (path === "/api/auth/login") {
      const body = request.postDataJSON() as { email: string; password: string };
      const user = USERS[body.email];
      if (!user || user.password !== body.password) return json(route, 401, { detail: "invalid credentials" });
      const token = `tok-${body.email}`;
      sessions.set(token, body.email);
      state.logins.push(body.email);
      return json(route, 200, { token, email: body.email, name: user.name, role: user.role });
    }
    if (path === "/api/stream") return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": connected\n\n" });
    if (!email) return json(route, 401, { detail: "missing bearer token" });
    const user = USERS[email]!;
    if (path === "/api/auth/me") return json(route, 200, { email, name: user.name, role: user.role });
    if (path === "/api/approvals") {
      const status = url.searchParams.get("status") ?? "pending";
      return json(route, 200, status === "all" ? state.approvals : state.approvals.filter((a) => a.status === status));
    }
    const one = path.match(/^\/api\/approvals\/(\d+)$/);
    if (one) {
      const found = state.approvals.find((a) => a.id === Number(one[1]));
      return found ? json(route, 200, found) : json(route, 404, { detail: "not found" });
    }
    const resolve = path.match(/^\/api\/approvals\/(\d+)\/resolve$/);
    if (resolve) {
      if (user.role === "viewer") return json(route, 403, { detail: "approver role required" });
      const id = Number(resolve[1]);
      const body = request.postDataJSON() as Record<string, unknown>;
      const found = state.approvals.find((a) => a.id === id);
      if (!found) return json(route, 404, { detail: "not found" });
      found.status = String(body.status);
      state.resolved.push({ id, body });
      return json(route, 200, { id, status: body.status, resolved_by: user.name, callback_status: "sent" });
    }
    if (path === "/api/board") return json(route, 200, state.board);
    if (path.startsWith("/api/board/") && path.endsWith("/detail"))
      return json(route, 200, { po_name: "P00015", lines: [{ line_id: 1, product: "[CBEA-LHN] Válvula", qty: 12, qty_received: 0, qty_invoiced: 0, price_unit: 104.16, subtotal: 1249.92, date_planned: null }], origin: { kind: "odoo", run_id: null, as_of: null, summary: null, explanations: [], created_by: "Administrator", origin: null } });
    if (path === "/api/performance/scores") return json(route, 200, []);
    if (path === "/api/mailbox/sync") return json(route, 200, { status: "ok", fetched: 0, linked: 0, unlinked: 0, ignored: 0, errors: 0, linked_po_names: [], message: "no new emails" });
    const move = path.match(/^\/api\/board\/(\w+)\/move$/);
    if (move && request.method() === "POST") {
      if (user.role === "viewer") return json(route, 403, { detail: "approver role required" });
      const body = request.postDataJSON() as Record<string, unknown>;
      state.moves.push({ po: move[1]!, body });
      return json(route, 200, { po_name: move[1], column: body.to, message: `${move[1]} confirmed` });
    }
    if (path === "/api/cases/case_a")
      return json(route, 200, {
        case: { case_id: "case_a", code: "C00001", kind: "rfq", po_name: "P00015", status: "awaiting_approval", created_at: "2026-09-11T08:00:00Z", updated_at: "2026-09-12T09:00:00Z" },
        events: [{ id: 1, at: "2026-09-11T08:00:00Z", kind: "task_sent", payload: { agent: "supplier_comms", task: "send_rfq" } }],
        runs: [],
      });
    if (path === "/api/cases") return json(route, 200, []);
    if (path.endsWith("/chat") && request.method() === "GET") return json(route, 200, []);
    if (path === "/api/exceptions") return json(route, 200, { as_of: "2026-09-14", late_pos: [], rfqs_no_reply: [], unlinked_mails: [], failed_runs: [], stale_approvals: [] });
    if (path === "/api/planning/runs") return json(route, 200, [state.run]);
    if (path === "/api/planning/calendar" && request.method() === "GET") return json(route, 200, []);
    if (path === "/api/planning/calendar" && request.method() === "POST") return json(route, 403, { detail: "approver role required" });
    if (path === "/api/risk") return json(route, 200, { as_of: "2026-09-14", warehouse_code: "WH", products: [], suppliers: [], cash_exposure: 0, at_risk_30: 0 });
    if (path === "/api/briefing") return json(route, 404, { detail: "no briefing yet" });
    if (path === "/api/demo") return json(route, 200, { steps: [{ key: "late_order_eta", title: "A late order gets a delivery-date request", say: "The department noticed.", click: "Approvals.", needs: "none" }, { key: "supplier_eta_reply", title: "The supplier answers with a new date", say: "From its own mailbox.", click: "Approvals; Board.", needs: "mailbox" }], position: 0, started_at: null, finished: false, next: { key: "late_order_eta", title: "A late order gets a delivery-date request", say: "The department noticed.", click: "Approvals.", needs: "none" }, outcomes: [], records: {}, ready: { mailbox: false, world: true, notes: ["SC__DEMO__SMTP_USER / SMTP_PASSWORD not set: the supplier's replies (steps 2, 4 and 5) cannot be sent"] } });
    if (path === "/api/home") return json(route, 200, { as_of: "2026-09-14", kpis: [{ key: "service_level", value: 70, previous: null, unit: "pct", currency: null, detail: "2 scored supplier(s)" }, { key: "late_orders", value: 1, unit: "count" }, { key: "pending_approvals", value: state.approvals.filter((a) => a.status === "pending").length, unit: "count" }, { key: "spend_month", value: 6514.44, previous: 500, unit: "money", currency: "USD" }, { key: "ai_cost_month", value: 0.08, previous: 0.1, unit: "usd" }, { key: "automated_week", value: 2, unit: "count" }], needs_you: state.approvals.filter((a) => a.status === "pending").map((a) => ({ text: `#${a.id} ${a.kind}: ${a.summary}`, path: `/approvals?id=${a.id}` })), pending: state.approvals.filter((a) => a.status === "pending").length, late: 1, silent: 0, automated_week: 2 });
    if (path === "/api/ai") return json(route, 200, { days: 30, since: "2026-08-15T00:00:00Z", automation: [{ kind: "send_email", automated: 3, decided: 1, rate: 0.75 }], automation_rate: 0.75, decisions: 1, turnaround_hours_median: 1.2, edit_rate: 0.5, rejection_rate: 0, eta_error_days: 4, wape_by_class: [{ abc_class: "A", wape: 0.16, lines: 2 }], invoices_first_time: 2, invoices_total: 2, invoices_first_time_rate: 1, negotiation_savings: null, runs: 2, cost_usd: 0.08, cases_with_runs: 1, cost_per_case_usd: 0.08, by_agent: [{ agent: "sourcing", runs: 1, cost_usd: 0.06 }] });
    if (path === "/api/suppliers/45") return json(route, 200, { partner_id: 45, partner_name: "Proveedor Hidraulica", score: { partner_id: 45, partner_name: "Proveedor Hidraulica", score: 75.5, otif: 0.5, lead_time_mean_days: 42.3 }, orders: [{ po_id: 15, po_name: "P00015", state: "sent", date_planned: "2026-09-20", amount_total: 1250, currency: "USD", receipt_status: null }], prices: [{ product_id: 1, product: "[CBEA-LHN] Valvula", supplier_code: null, price: 104.16, currency: "USD", min_qty: 1, lead_days: 28, valid_from: null }], rounds: [], emails: [{ po_name: "P00015", direction: "out", at: "2026-09-12T09:00:00Z", web_link: "https://outlook/out", confidence: "exact" }], products: [{ product_id: 1, product: "[CBEA-LHN] Valvula", rank: 1, suppliers: 2, best_partner_name: "Proveedor Hidraulica", best_score: 75.5 }] });
    if (path === "/api/push/key") return json(route, 200, { enabled: false, public_key: null });
    if (path === "/api/approvals/bulk" && request.method() === "POST") {
      const body = request.postDataJSON() as { ids: number[]; status: string };
      state.bulk.push(body);
      state.approvals = state.approvals.map((a) => (body.ids.includes(a.id) ? { ...a, status: body.status } : a));
      return json(route, 200, { resolved: body.ids.length, results: body.ids.map((id) => ({ id, status: body.status, error: null })) });
    }
    if (path === "/api/briefing/history") return json(route, 200, []);
    if (path === "/api/assistant" && request.method() === "GET") return json(route, 200, []);
    if (path === "/api/planning/runs/run_1") return json(route, 200, { run: state.run, lines: state.lines });
    if (path === "/api/planning/runs/run_1/ranking") return json(route, 200, { run_id: "run_1", better_count: 0, lines: [] });
    if (path.startsWith("/api/planning/runs/run_1/lines/")) return json(route, 200, { line_id: "run_1:102", product_id: 102, forecast_daily: 2, sigma_daily: 0.5, days: [{ day: "2026-09-13", ordered: 3, delivered: 3 }] });
    if (path === "/api/runs") return json(route, 200, []);
    if (path === "/api/runs/scheduler") return json(route, 200, []);
    if (path === "/api/settings" && request.method() === "GET")
      return json(route, 200, { version: 0, changed_by: "environment", changed_at: "2026-09-14T00:00:00Z", settings: { model_by_agent: {}, rfq_no_reply_days: [3, 7], po_eta_request_before_days: 5, po_late_days: [1, 4], approval_stale_days: 2, approval_expire_days: 7, max_actions_per_run: 20, auto_send_partner_ids: [], auto_send_kinds: [], ignored_senders: [], internal_senders: [], briefing_recipients: [], planning_service_level: null, planning_review_period_days: null, planning_max_coverage_days: null, holding_cost_pct_year: 20, sourcing_top_n: 3, sourcing_deadline_days: 5, sourcing_freight_pct: 5, negotiation_cap_pct: 10, negotiation_max_rounds: 2 } });
    if (path === "/api/settings/models") return json(route, 200, []);
    if (path === "/api/autonomy" && request.method() === "GET") return json(route, 200, { version: 0, changed_by: "environment", changed_at: null, policy: { rules: [] }, pending: null });
    if (path === "/api/autonomy/actions") return json(route, 200, []);
    if (path === "/api/playbooks" && request.method() === "GET")
      return json(route, 200, [{ name: "late_order", title: "Late order", description: "Ask, wait, chase.", trigger: "po_late", active_runs: 0, steps: [{ id: "ask_eta", label: "Ask the supplier for a new delivery date", kind: "agent", when: "not_received", agent: "supplier_comms", task: "request_eta", wait_days: null, until: null, action: null, active_runs: 0 }] }]);
    if (path === "/api/playbooks/runs" && request.method() === "GET") return json(route, 200, []);
    if (path === "/api/playbooks/runs" && request.method() === "POST") return json(route, 403, { detail: "approver role required" });
    if (path === "/api/sourcing/rounds" && request.method() === "GET") return json(route, 200, []);
    if (path.startsWith("/api/sourcing/") && request.method() === "POST") return json(route, 403, { detail: "approver role required" });
    if (path === "/api/learning/suggestions") return json(route, 200, []);
    if (path === "/api/learning/stats") return json(route, 200, { days: 90, total: 0, unchanged: 0, edited: 0, rejected: 0, expired: 0, by_kind: [], by_supplier: [] });
    if (path.startsWith("/api/learning/profiles/")) return json(route, 200, { partner_id: 45, language: null, formality: null, greeting: null, sign_off: null, contacts: [], notes: "", facts: {}, updated_at: null, updated_by: null });
    if (path === "/api/settings/history") return json(route, 200, []);
    return json(route, 404, { detail: `unhandled ${request.method()} ${path}` });
  });
}

export async function login(page: Page, email: string, password = "s3cret!!"): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}
