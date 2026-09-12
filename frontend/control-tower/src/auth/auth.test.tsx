import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Routed } from "@/App";
import { I18nProvider } from "@/i18n";
import { AuthProvider } from "./AuthProvider";
import { authStore } from "./store";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function renderApp() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <I18nProvider initial="en">
        <AuthProvider>
          <Routed />
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("auth guard and login", () => {
  beforeEach(() => {
    authStore.clear();
    window.history.replaceState(null, "", "/cases");
  });
  afterEach(() => vi.restoreAllMocks());

  it("redirects a visitor to the login page and back after signing in", async () => {
    const seen: Request[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(String(input), init);
      seen.push(request);
      if (request.url.endsWith("/api/auth/login")) {
        expect(await request.json()).toEqual({ email: "ana@x.com", password: "s3cret!!" });
        return jsonResponse(200, { token: "jwt-1", email: "ana@x.com", name: "Ana", role: "approver" });
      }
      if (request.url.endsWith("/api/auth/me")) {
        return jsonResponse(200, { email: "ana@x.com", name: "Ana", role: "approver" });
      }
      return jsonResponse(404, { detail: "no" });
    });
    renderApp();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Email"), "ana@x.com");
    await userEvent.type(screen.getByLabelText("Password"), "s3cret!!");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(window.location.pathname).toBe("/cases"));
    expect(await screen.findByRole("heading", { name: "History" })).toBeInTheDocument();
    expect(authStore.getToken()).toBe("jwt-1");
    const me = seen.find((request) => request.url.endsWith("/api/auth/me"));
    expect(me?.headers.get("authorization")).toBe("Bearer jwt-1"); // the client adds the session
  });

  it("shows the failure text on a 401", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(401, { detail: "invalid credentials" }));
    renderApp();
    await screen.findByRole("heading", { name: "Sign in" });
    await userEvent.type(screen.getByLabelText("Email"), "ana@x.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");
  });

  it("hides settings from non-admins and signs out", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, { email: "vic@x.com", name: "Vic", role: "viewer" }),
    );
    authStore.set({ token: "jwt-2", user: { email: "vic@x.com", name: "Vic", role: "viewer" } });
    renderApp();
    expect(await screen.findByRole("heading", { name: "History" })).toBeInTheDocument();
    expect(screen.queryByText("Settings")).not.toBeInTheDocument();
    await userEvent.click(screen.getAllByText("Sign out")[0]!);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(authStore.getToken()).toBeNull();
  });
});
