import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { AssistantMessage } from "./AssistantPage";

let messages: AssistantMessage[];
let calls: string[];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function director(id: number, text: string, extra: Partial<AssistantMessage> = {}): AssistantMessage {
  return { id, at: "2026-09-14T09:00:00Z", role: "director", text, by: null, citations: [], plan: null, plan_status: null, outcome: null, ...extra };
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (url.pathname === "/api/assistant" && request.method === "GET") return jsonResponse(200, messages);
  if (url.pathname === "/api/assistant" && request.method === "POST") {
    const body = (await request.json()) as { text: string };
    calls.push(`ask:${body.text}`);
    const asked: AssistantMessage = { id: messages.length + 1, at: "2026-09-14T09:00:00Z", role: "user", text: body.text, by: "ana@x.com", citations: [], plan: null, plan_status: null, outcome: null };
    const answered = director(messages.length + 2, "I will ask the market for 40 valves.", {
      citations: [{ ref: "CBEA-LHN", label: "Valvula", path: "/risk" }, { ref: "P00077", label: "P00077", path: "/?po=P00077" }],
      plan: { summary: "A quote round for the valve", steps: [{ kind: "quote_round", product_ref: "CBEA-LHN", product_id: 1, qty: 40, partner_ids: [], deadline_days: 5, po_name: null, until: null, playbook: null, note: null, explanation: "Ask the suppliers who list CBEA-LHN for 40 units." }] },
      plan_status: "proposed",
    });
    messages = [...messages, asked, answered];
    return jsonResponse(200, { messages: [asked, answered] });
  }
  const confirm = url.pathname.match(/^\/api\/assistant\/(\d+)\/confirm$/);
  if (confirm) {
    calls.push(`confirm:${confirm[1]}`);
    messages = messages.map((m) => (m.id === Number(confirm[1]) ? { ...m, plan_status: "confirmed", outcome: "quote_round CBEA-LHN: quote round #5 started" } : m));
    return jsonResponse(200, messages.find((m) => m.id === Number(confirm[1])));
  }
  if (url.pathname === "/api/approvals") return jsonResponse(200, []);
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

describe("department assistant", () => {
  beforeEach(() => {
    messages = [];
    calls = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
  });
  afterEach(() => vi.restoreAllMocks());

  it("asks, shows the sources as links and runs the plan only when confirmed", async () => {
    renderAt("/assistant");
    expect(await screen.findByRole("heading", { name: "Ask your AI" })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Message to your AI"), "Start a quote round for CBEA-LHN, 40 units");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls).toEqual(["ask:Start a quote round for CBEA-LHN, 40 units"]));
    expect(await screen.findByText("I will ask the market for 40 valves.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CBEA-LHN" })).toHaveAttribute("href", "/risk");
    expect(screen.getByRole("link", { name: "P00077" })).toHaveAttribute("href", "/?po=P00077");
    const plan = screen.getByTestId("proposed-plan");
    expect(plan).toHaveTextContent("A quote round for the valve");
    expect(plan).toHaveTextContent("Quote round · CBEA-LHN × 40");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the plan" }));
    await waitFor(() => expect(calls).toContain("confirm:2"));
    expect(await screen.findByText("quote_round CBEA-LHN: quote round #5 started")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm the plan" })).toBeNull();
  });
});
