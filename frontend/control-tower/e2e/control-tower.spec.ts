import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { login, makeState, mockApi, type ApiState } from "./api";

let state: ApiState;

test.beforeEach(async ({ page }) => {
  state = makeState();
  await mockApi(page, state);
});

test("an approver lands on the board, opens an order and confirms it from the panel", async ({ page }) => {
  await login(page, "ana@x.com");
  await expect(page).toHaveURL(/\/(\?.*)?$/);
  const incoming = page.getByRole("region", { name: "To receive" });
  await expect(incoming.getByRole("article", { name: "P00016" })).toContainText("2 d late");
  await expect(page.getByRole("link", { name: "Review the plan" })).toHaveAttribute("href", "/planning/run_1");
  await page.getByRole("region", { name: "Quotation requested" }).getByRole("button", { name: "Open P00015" }).click();
  const drawer = page.getByRole("dialog", { name: "Order P00015" });
  await expect(drawer).toContainText("RFQ sent, waiting for the supplier");
  await expect(drawer.getByTestId("email-preview")).toContainText("Dear supplier"); // the pending approval, in place
  await expect(drawer.getByRole("region", { name: "History" })).toContainText("Task sent");
  await drawer.getByRole("button", { name: "Confirm the order" }).click();
  await expect.poll(() => state.moves.length).toBe(1);
  expect(state.moves[0]).toEqual({ po: "P00015", body: { to: "confirmed", note: null } });
  await expect(drawer.getByRole("status")).toContainText("P00015 confirmed");
});

test("an approver reads the email safely and approves it in the inbox", async ({ page, isMobile }) => {
  await login(page, "ana@x.com");
  await page.goto("/approvals");
  const row = page.getByRole("button", { name: /Send follow-up to Proveedor/ });
  await expect(row).toBeVisible();
  if (isMobile) await row.click(); // the phone shows the list first, then the detail
  const preview = page.getByTestId("email-preview");
  await expect(preview).toContainText("Dear supplier");
  expect(await preview.locator("img").count()).toBe(0);
  await expect(page.getByText("rfq_silent: no reply to the RFQ for 3 days")).toBeVisible();

  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect.poll(() => state.resolved.length).toBe(1);
  expect(state.resolved[0]).toEqual({ id: 1, body: { status: "approved", reason: null, edited_payload: null } });
  await expect(page.getByRole("button", { name: /Send follow-up to Proveedor/ })).toHaveCount(0);
});

test("a planner reviews the run, edits a quantity and approves the selected lines", async ({ page }) => {
  await login(page, "ana@x.com");
  await page.goto("/planning/run_1");
  await expect(page.getByRole("heading", { name: /Proveedor Hidraulica/ })).toBeVisible();
  await page.getByLabel("HYD-101 qty").fill("30");
  await page.getByRole("checkbox", { name: "HYD-102", exact: true }).uncheck();
  await page.getByRole("button", { name: "Approve 1 line(s)" }).click();
  await expect.poll(() => state.resolved.length).toBe(1);
  expect(state.resolved[0]).toEqual({
    id: 2,
    body: { status: "approved", edited_payload: { accepted_line_ids: ["run_1:101"], edits: { "run_1:101": { order_qty: 30 } } } },
  });
  await page.getByRole("button", { name: "HYD-102" }).click();
  await expect(page.getByRole("complementary", { name: "Line details" })).toContainText("Demand doubled.");
});

test("a viewer sees no actions and no settings; the language switch works", async ({ page }) => {
  await login(page, "vic@x.com");
  await expect(page.getByRole("article", { name: "P00016" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Drag P/ })).toHaveCount(0); // viewers cannot move cards
  await page.goto("/approvals");
  await expect(page.getByRole("button", { name: /Send follow-up/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Settings" })).toHaveCount(0);
  await page.goto("/settings");
  await expect(page).toHaveURL(/\/(\?.*)?$/);
  await page.getByRole("button", { name: "es", exact: true }).or(page.getByRole("button", { name: "Español" })).first().click();
  await expect(page.getByRole("region", { name: "Por recibir" })).toBeVisible();
});

test("a wrong password stays on the login page", async ({ page }) => {
  await login(page, "ana@x.com", "nope");
  await expect(page.getByRole("alert")).toHaveText("Wrong email or password.");
  await expect(page).toHaveURL(/\/login/);
});

test("the board, the inbox and the planning review have no serious accessibility violations", async ({ page }) => {
  await login(page, "ana@x.com");
  await expect(page.getByRole("article", { name: "P00016" })).toBeVisible();
  const board = await new AxeBuilder({ page }).analyze();
  expect(board.violations.filter((v) => v.impact === "critical" || v.impact === "serious")).toEqual([]);
  await page.goto("/approvals");
  await expect(page.getByRole("button", { name: /Send follow-up/ })).toBeVisible();
  const inbox = await new AxeBuilder({ page }).analyze();
  expect(inbox.violations.filter((v) => v.impact === "critical" || v.impact === "serious")).toEqual([]);
  await page.goto("/planning/run_1");
  await expect(page.getByRole("heading", { name: /Proveedor Hidraulica/ })).toBeVisible();
  const planning = await new AxeBuilder({ page }).analyze();
  expect(planning.violations.filter((v) => v.impact === "critical" || v.impact === "serious")).toEqual([]);
});
