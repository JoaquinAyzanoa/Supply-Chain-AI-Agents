import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { authStore } from "@/auth/store";
import { I18nProvider } from "@/i18n";
import type { Briefing } from "./BriefingPage";

const BRIEFING: Briefing = {
  day: "2026-09-14",
  language: "en",
  since: "2026-09-13T12:00:00Z",
  paragraph: "Two decisions wait for you; the valve CBEA-LHN needs a source today.",
  counts: { needs_you: 2, risks: 1, late: 1, overnight: 3, ran_alone: 1, playbooks: 1 },
  emailed_to: [],
  created_at: "2026-09-14T07:30:00Z",
  sections: [
    { key: "needs_you", title: "Needs a decision today", count: 2, items: [{ text: "#17 award: Round #1 on P00081 · 4,200 USD", path: "/approvals?id=17" }, { text: "#24 internal request: 2 item(s)", path: "/approvals?id=24" }] },
    { key: "risks", title: "Stock and supplier risks", count: 1, items: [{ text: "CBEA-LHN: 62% chance of a stockout within 30 days", path: "/risk", probability: 0.62 }] },
    { key: "late", title: "Late and silent orders", count: 1, items: [{ text: "P00077: 7 day(s) past its date without a receipt", path: "/?po=P00077", days: 7 }] },
    { key: "overnight", title: "Since the last briefing", count: 3, items: [] },
    { key: "ran_alone", title: "Ran alone under the rules", count: 1, items: [{ text: "Reminder sent to Proveedor Hidraulica for P00080 · rule reminders", path: "/autonomy" }] },
    { key: "playbooks", title: "Plans in progress", count: 1, items: [{ text: "Late order: 1 active (1 × ask_eta)", path: "/playbooks" }] },
  ],
};

let briefing: Briefing | null;
let posted: string[];
let me: { email: string; name: string; role: string };

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(String(input), init);
  const url = new URL(request.url);
  if (url.pathname === "/api/auth/me") return jsonResponse(200, me);
  if (url.pathname === "/api/briefing" && request.method === "GET") return briefing ? jsonResponse(200, briefing) : jsonResponse(404, { detail: "no briefing yet" });
  if (url.pathname === "/api/briefing/history") return jsonResponse(200, briefing ? [briefing] : []);
  if (url.pathname === "/api/briefing/run") {
    posted.push("run");
    briefing = BRIEFING;
    return jsonResponse(201, BRIEFING);
  }
  if (url.pathname === "/api/briefing/email") {
    posted.push(`email:${JSON.stringify(await request.json())}`);
    return jsonResponse(200, { day: "2026-09-14", sent_to: ["ana@x.com"] });
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

describe("morning briefing", () => {
  beforeEach(() => {
    briefing = null;
    posted = [];
    me = { email: "ana@x.com", name: "Ana", role: "approver" };
    vi.spyOn(globalThis, "fetch").mockImplementation(fakeFetch);
    authStore.set({ token: "jwt", user: me as never });
  });
  afterEach(() => vi.restoreAllMocks());

  it("builds today's briefing on request, shows the paragraph and the sections, and emails it", async () => {
    renderAt("/briefing");
    expect(await screen.findByRole("heading", { name: "Morning briefing" })).toBeInTheDocument();
    expect(screen.getByText(/No briefing yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Build now" }));
    await waitFor(() => expect(posted).toEqual(["run"]));
    expect(await screen.findByText(/Two decisions wait for you/)).toBeInTheDocument();
    const needs = screen.getByRole("region", { name: "Needs a decision today" });
    expect(within(needs).getByText("2")).toBeInTheDocument();
    expect(within(needs).getByRole("link", { name: /#17 award/ })).toHaveAttribute("href", "/approvals?id=17");
    const late = screen.getByRole("region", { name: "Late and silent orders" });
    expect(within(late).getByRole("link", { name: /P00077/ })).toHaveAttribute("href", "/?po=P00077");
    expect(within(screen.getByRole("region", { name: "Since the last briefing" })).getByText("Nothing here.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Send this briefing to"), "ana@x.com");
    await userEvent.click(screen.getByRole("button", { name: "Send by email" }));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1]).toBe('email:{"to":["ana@x.com"]}');
    expect(await screen.findByRole("status")).toHaveTextContent("Sent to ana@x.com.");
  });

  it("shows a viewer the briefing without the build and email controls", async () => {
    briefing = BRIEFING;
    me = { email: "vic@x.com", name: "Vic", role: "viewer" };
    authStore.set({ token: "jwt", user: me as never });
    renderAt("/briefing");
    expect(await screen.findByText(/Two decisions wait for you/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Build now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send by email" })).toBeNull();
  });
});
