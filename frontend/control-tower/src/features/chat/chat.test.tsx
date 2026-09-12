import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { CaseDetail } from "@/features/cases/api";
import type { ChatMessage } from "./CaseChat";

const DETAIL: CaseDetail = {
  case: {
    case_id: "case_a",
    code: "C00001",
    kind: "eta",
    status: "escalated",
    po_name: "P00066",
    summary: "7 days late",
    created_at: "2026-09-10T10:00:00Z",
    updated_at: "2026-09-12T10:00:00Z",
  },
  events: [],
  runs: [],
};

let messages: ChatMessage[];
let posted: { path: string; body: unknown }[];
let nextId = 10;

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  const path = decodeURIComponent(url.pathname);
  if (path === "/api/auth/me") return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
  if (path === "/api/cases/C00001") return jsonResponse(200, DETAIL);
  if (path === "/api/cases/C00001/chat" && request.method === "GET") return jsonResponse(200, messages);
  if (path === "/api/cases/C00001/chat" && request.method === "POST") {
    const body = (await request.json()) as { text: string };
    posted.push({ path, body });
    const asked: ChatMessage = { id: nextId++, at: "2026-09-12T11:00:00Z", role: "user", text: body.text, by: "ana@x.com" };
    const answered: ChatMessage = {
      id: nextId++,
      at: "2026-09-12T11:00:05Z",
      role: "director",
      text: "I will ask Proveedor Hidraulica for a firm date.",
      action: { kind: "request_eta", note: "7 days late", explanation: "Ask the supplier for a firm delivery date on P00066." },
      action_status: "proposed",
    };
    messages = [...messages, asked, answered];
    return jsonResponse(200, { messages: [asked, answered] });
  }
  const confirm = path.match(/^\/api\/cases\/C00001\/chat\/(\d+)\/confirm$/);
  if (confirm && request.method === "POST") {
    posted.push({ path, body: null });
    const id = Number(confirm[1]);
    messages = messages.map((m) => (m.id === id ? { ...m, action_status: "confirmed" as const } : m));
    const done: ChatMessage = { id: nextId++, at: "2026-09-12T11:01:00Z", role: "director", text: "Delivery date request sent.", by: "ana@x.com" };
    messages = [...messages, done];
    return jsonResponse(200, { messages: [done] });
  }
  return jsonResponse(404, { detail: `unhandled ${request.method} ${path}` });
}

describe("case chat", () => {
  beforeEach(() => {
    messages = [];
    posted = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: { email: "ana@x.com", name: "Ana", role: "approver" } });
    window.history.replaceState(null, "", "/cases/C00001");
  });
  afterEach(() => vi.restoreAllMocks());

  it("asks the director, shows the proposed action and confirms it", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nProvider initial="en">
          <AuthProvider>
            <Routed />
          </AuthProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    const panel = await screen.findByRole("region", { name: "Talk to the director" });
    expect(panel).toHaveTextContent("Ask what is going on");
    await userEvent.type(within(panel).getByLabelText("Message to the director"), "Ask them for a firm date{Enter}");
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]?.body).toEqual({ text: "Ask them for a firm date" });
    expect(within(panel).getByLabelText("Message to the director")).toHaveValue(""); // emptied at once
    const action = await within(panel).findByTestId("proposed-action");
    expect(action).toHaveTextContent("Ask the supplier for a delivery date");
    expect(action).toHaveTextContent("Ask the supplier for a firm delivery date on P00066.");

    await userEvent.click(within(action).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1]?.path).toMatch(/\/chat\/11\/confirm$/);
    await waitFor(() => expect(panel).toHaveTextContent("Delivery date request sent."));
    expect(within(panel).getByText("done")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });
});
