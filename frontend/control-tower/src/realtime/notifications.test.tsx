import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsBell } from "@/components/layout/NotificationsBell";
import { I18nProvider } from "@/i18n";
import { notifications, notifyFor } from "./notifications";

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("notifications", () => {
  beforeEach(() => {
    notifications.clear();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      if (url.pathname === "/api/approvals/7")
        return jsonResponse(200, { id: 7, kind: "send_email", status: "pending", summary: "Send reminder to Wood Corner", payload: {}, created_at: "2026-09-14T08:00:00Z" });
      if (url.pathname === "/api/cases/case_x")
        return jsonResponse(200, { case: { case_id: "case_x", code: "C00007", kind: "eta", po_name: "P00006", status: "escalated", summary: "4 days late", created_at: "2026-09-10T08:00:00Z", updated_at: "2026-09-14T08:00:00Z" }, events: [], runs: [] });
      return jsonResponse(404, {});
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("turns server events into lines and counts the unread ones", async () => {
    await notifyFor("approval_created", JSON.stringify({ approval_id: 7 }));
    await notifyFor("approval_created", JSON.stringify({ approval_id: 7 })); // once
    await notifyFor("case_updated", JSON.stringify({ case_id: "case_x", status: "escalated" }));
    await notifyFor("case_updated", JSON.stringify({ case_id: "case_x", event: "note" })); // routine: no line
    await notifyFor("run_finished", JSON.stringify({ run_id: "run_1", status: "failed" }));
    await notifyFor("run_finished", "not json");
    expect(notifications.get().map((n) => n.kind)).toEqual(["run_failed", "escalation", "approval"]);

    render(
      <I18nProvider initial="en">
        <NotificationsBell />
      </I18nProvider>,
    );
    const bell = screen.getByRole("button", { name: "3 new notifications" });
    await userEvent.click(bell);
    const list = screen.getByRole("region", { name: "Notifications" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(list).toHaveTextContent("Needs a person");
    expect(list).toHaveTextContent("C00007 · P00006 · 4 days late");
    expect(list).toHaveTextContent("Send reminder to Wood Corner");
    expect(screen.getByRole("button", { name: "Notifications" })).toBeInTheDocument(); // read now

    act(() => notifications.push({ id: "x", kind: "approval", text: "another", to: { path: "/approvals" } }));
    expect(screen.getByRole("button", { name: "1 new notifications" })).toBeInTheDocument();
  });
});
