/**
 * Record the demo day as a captioned video, on the live stack (English).
 *
 *   SC_UI_PASSWORD=... node scripts/demo-video.mjs [--out ../../demo-video] [--speed 2]
 *
 * The scenario is the Director agent's own (POST /api/demo/next, see docs/demo.md): each step
 * runs through the API while the browser tours the screens that matter, so the time a real
 * email takes to travel is spent showing the product. The film shows who wrote what (the
 * supplier's emails are shown as they were sent; the agents' drafts are labelled), the chat
 * typed live, Odoo and the order PDF, and the agents' traces in Langfuse. Approvals are
 * decided on screen. A timeline of the take is kept so the dull parts (waiting for mail)
 * are fast-forwarded harder than the parts meant to be read; ffmpeg then writes the .mp4.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";

import { chromium } from "@playwright/test";

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const BASE = (process.env.SC_DIRECTOR_URL ?? "http://localhost:8010").replace(/\/$/, "");
const ODOO = (process.env.SC_ODOO_URL ?? "http://localhost:8069").replace(/\/$/, "");
const LANGFUSE = (process.env.SC_LANGFUSE_URL ?? "http://localhost:3000").replace(/\/$/, "");
const USER = process.env.SC_DEMO_USER ?? "admin@scai.dev";
const PASSWORD = process.env.SC_UI_PASSWORD;
const ODOO_LOGIN = process.env.SC_ODOO_LOGIN ?? "admin";
const ODOO_PASSWORD = process.env.SC_ODOO_PASSWORD ?? "admin";
const LANGFUSE_USER = process.env.SC_LANGFUSE_USER ?? "admin@scai.local";
const LANGFUSE_PASSWORD = process.env.SC_LANGFUSE_PASSWORD ?? "scai-admin-password";
const OUT_DIR = resolve(flag("out", "../../demo-video"));
const SPEED = Number(flag("speed", "2")); // what is meant to be read plays at this speed
const WAIT_SPEED = SPEED * 5; // waiting for a real email plays much faster
const SIZE = { width: 1600, height: 900 };
const APPROVE_BUTTON = /^Approve( \d+ line\(s\)| with edits)?$|^(Award|Send the counter-offer)$/;

// --- the director's API ---------------------------------------------------------------------------
let token = "";
async function api(path, method = "GET", body) {
  let response;
  for (let attempt = 0; ; attempt += 1) {
    try {
      response = await fetch(`${BASE}${path}`, {
        method,
        headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      break;
    } catch (error) {
      // a dropped keep-alive connection: reads are safe to repeat, writes are not
      if (attempt >= 3 || method !== "GET") throw error;
      await new Promise((done) => setTimeout(done, 1500));
    }
  }
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const log = (...parts) => console.log(new Date().toISOString().slice(11, 19), ...parts);

// --- the take's timeline: which seconds are read, which are waited through -------------------------
let filmStart = 0;
const timeline = []; // [{ at: seconds, speed }]
function pace(speed) {
  const at = (Date.now() - filmStart) / 1000;
  if (timeline.length && timeline[timeline.length - 1].speed === speed) return;
  timeline.push({ at, speed });
}
/** Hold the screen long enough to read `words` once the film plays at its speed. */
async function read(words, extraMs = 0) {
  pace(SPEED);
  const seconds = Math.max(3.2, words / 3.0 + 1.2);
  await sleep(seconds * 1000 * SPEED + extraMs);
}

/** Run one demo step, asking again while it waits for the mailbox; resolves with its outcome. */
function startStep(key, { approve = false, leave = [] } = {}) {
  const state = { done: false, outcome: null, view: null };
  state.promise = (async () => {
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const view = await api("/api/demo/next", "POST", { approve, step: key, leave });
      const outcome = view.outcomes.find((o) => o.key === key);
      state.view = view;
      state.outcome = outcome;
      if (!outcome || outcome.status !== "waiting") break;
      log(`  ~ ${key} waiting: ${outcome.summary.slice(0, 100)}`);
    }
    state.done = true;
    log(`step ${key}: ${state.outcome?.status} | ${state.outcome?.summary?.slice(0, 140)}`);
    return state.outcome;
  })().catch((error) => {
    state.done = true;
    state.outcome = { key, status: "failed", summary: String(error), approval_ids: [], links: [] };
    log(`step ${key} broke: ${error}`);
    return state.outcome;
  });
  return state;
}

// --- the screen ---------------------------------------------------------------------------------
let page;
let current = { chapter: "", text: "", who: "" };

const OVERLAY_CSS = `
#demo-caption{position:fixed;left:0;right:0;bottom:0;z-index:2147483640;padding:46px 44px 22px;color:#fff;pointer-events:none;
 background:linear-gradient(180deg,rgba(8,12,22,0) 0%,rgba(8,12,22,.94) 34%);font-family:Inter,"Segoe UI",system-ui,sans-serif}
#demo-caption .chapter{font-size:15px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#7dd3fc}
#demo-caption .text{font-size:25px;line-height:1.32;font-weight:500;max-width:1380px;margin-top:4px}
#demo-who{position:fixed;top:14px;right:18px;z-index:2147483641;padding:8px 16px;border-radius:999px;font:700 15px Inter,"Segoe UI",system-ui,sans-serif;
 letter-spacing:.04em;color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.35);pointer-events:none}
#demo-who.agent{background:#2563eb}#demo-who.supplier{background:#ea580c}#demo-who.person{background:#059669}#demo-who.system{background:#6d28d9}
.demo-box{position:fixed;z-index:2147483639;border:3px solid #f59e0b;border-radius:10px;box-shadow:0 0 0 4000px rgba(8,12,22,.38);pointer-events:none}
.demo-box-label{position:fixed;z-index:2147483642;background:#f59e0b;color:#111;font:700 14px Inter,"Segoe UI",system-ui,sans-serif;padding:4px 10px;border-radius:6px;pointer-events:none}
#demo-cursor{position:fixed;left:-40px;top:-40px;width:28px;height:28px;margin:-14px 0 0 -14px;border-radius:50%;z-index:2147483647;
 background:rgba(56,189,248,.35);border:2px solid #38bdf8;transition:left .7s ease,top .7s ease,transform .15s;pointer-events:none}
#demo-mail{position:fixed;inset:0;z-index:2147483638;display:flex;align-items:flex-start;justify-content:center;padding-top:70px;background:rgba(8,12,22,.74);font-family:Inter,"Segoe UI",system-ui,sans-serif}
#demo-mail .sheet{width:920px;max-height:640px;overflow:hidden;background:#fff;color:#0f172a;border-radius:14px;box-shadow:0 30px 80px rgba(0,0,0,.5);border-top:10px solid #ea580c}
#demo-mail .head{padding:18px 26px 12px;border-bottom:1px solid #e2e8f0;font-size:15px;line-height:1.6}
#demo-mail .head b{display:inline-block;width:70px;color:#64748b;font-weight:600}
#demo-mail .tag{display:inline-block;margin-bottom:8px;padding:3px 10px;border-radius:999px;background:#ffedd5;color:#9a3412;font-weight:700;font-size:13px;letter-spacing:.05em}
#demo-mail pre{margin:0;padding:18px 26px 24px;font:17px/1.5 Inter,"Segoe UI",system-ui,sans-serif;white-space:pre-wrap}
`;

async function overlay() {
  await page
    .evaluate(
      ([state, css]) => {
        if (!document.getElementById("demo-style")) {
          const style = document.createElement("style");
          style.id = "demo-style";
          style.textContent = css;
          document.head.appendChild(style);
        }
        let bar = document.getElementById("demo-caption");
        if (!bar) {
          bar = document.createElement("div");
          bar.id = "demo-caption";
          bar.innerHTML = '<div class="chapter"></div><div class="text"></div>';
          document.body.appendChild(bar);
          const dot = document.createElement("div");
          dot.id = "demo-cursor";
          document.body.appendChild(dot);
        }
        bar.querySelector(".chapter").textContent = state.chapter;
        bar.querySelector(".text").textContent = state.text;
        let who = document.getElementById("demo-who");
        if (!state.who) who?.remove();
        else {
          if (!who) {
            who = document.createElement("div");
            who.id = "demo-who";
            document.body.appendChild(who);
          }
          const [kind, label] = state.who.split("|");
          who.className = kind;
          who.textContent = label;
        }
      },
      [current, OVERLAY_CSS],
    )
    .catch(() => undefined);
}

const WHO = {
  agent: (name) => `agent|✎ WRITTEN BY THE ${name.toUpperCase()}`,
  supplier: (name) => `supplier|✉ EMAIL FROM THE SUPPLIER · ${name.toUpperCase()}`,
  person: "person|✓ A PERSON DECIDES",
  odoo: "system|ODOO · THE SYSTEM OF RECORD",
  langfuse: "system|LANGFUSE · WHAT THE MODEL SAW AND ANSWERED",
};

/** Caption the screen and hold it for as long as the text takes to read. */
async function say(chapter, text, { who = "", extraMs = 0 } = {}) {
  current = { chapter, text, who };
  await overlay();
  await read(text.split(/\s+/).length, extraMs);
}

async function go(url, { settle = 1200 } = {}) {
  pace(WAIT_SPEED); // loading is not worth watching
  const target = url.startsWith("http") ? url : `${BASE}${url}`;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await page.goto(target, { waitUntil: "domcontentloaded", timeout: 45000 });
      break;
    } catch (error) {
      log(`  ${url} did not load (attempt ${attempt + 1}): ${String(error).slice(0, 60)}`);
      await sleep(4000);
    }
  }
  await sleep(settle);
  await page
    .waitForFunction(() => !/Loading…|Loading\.\.\./.test(document.querySelector("main")?.innerText ?? ""), null, { timeout: 20000 })
    .catch(() => undefined);
  await sleep(400);
  await overlay();
}

async function scroll(top, { within } = {}) {
  await page
    .evaluate(
      ([y, selector]) => {
        const target = selector ? document.querySelector(selector) : null;
        (target ?? window).scrollBy({ top: y, behavior: "smooth" });
      },
      [top, within ?? null],
    )
    .catch(() => undefined);
  await sleep(900);
}

/** Put a spotlight on part of the screen, with a label. */
async function spotlight(locator, label) {
  await clearSpotlight();
  try {
    const target = locator.first();
    await target.waitFor({ state: "visible", timeout: 4000 });
    await target.scrollIntoViewIfNeeded();
    await sleep(300);
    const box = await target.boundingBox();
    if (!box) return false;
    await page.evaluate(
      ([b, text]) => {
        const height = Math.min(b.height, window.innerHeight - b.y - 150);
        const el = document.createElement("div");
        el.className = "demo-box";
        el.style.cssText = `left:${b.x - 6}px;top:${b.y - 6}px;width:${b.width + 12}px;height:${height + 12}px`;
        document.body.appendChild(el);
        if (text) {
          const tag = document.createElement("div");
          tag.className = "demo-box-label";
          tag.textContent = text;
          tag.style.cssText = `left:${b.x - 6}px;top:${Math.max(4, b.y - 36)}px`;
          document.body.appendChild(tag);
        }
      },
      [box, label ?? ""],
    );
    return true;
  } catch {
    return false;
  }
}
async function clearSpotlight() {
  await page.evaluate(() => document.querySelectorAll(".demo-box,.demo-box-label").forEach((el) => el.remove())).catch(() => undefined);
}

async function card(title, text, seconds) {
  pace(SPEED);
  await page.evaluate(
    ([heading, body]) => {
      const el = document.createElement("div");
      el.id = "demo-card";
      el.style.cssText =
        "position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;" +
        "background:#0b1020;color:#fff;font-family:Inter,Segoe UI,system-ui,sans-serif;text-align:center;padding:0 140px";
      el.innerHTML = `<div style="font-size:54px;font-weight:700;line-height:1.15"></div><div style="font-size:25px;color:#cbd5e1;line-height:1.4;max-width:1150px"></div>`;
      el.children[0].textContent = heading;
      el.children[1].textContent = body;
      document.body.appendChild(el);
    },
    [title, text],
  );
  await sleep(seconds * 1000 * SPEED);
  await page.evaluate(() => document.getElementById("demo-card")?.remove());
}

/** Show an email a supplier sent, as the supplier sent it. */
async function supplierMail(mail, chapter, text) {
  await page.evaluate((m) => {
    const el = document.createElement("div");
    el.id = "demo-mail";
    el.innerHTML =
      '<div class="sheet"><div class="head"><span class="tag">INBOUND EMAIL · WRITTEN BY THE SUPPLIER</span><div><b>From</b><span class="f"></span></div><div><b>To</b>Purchasing Team &lt;scai.compras@outlook.com&gt;</div><div><b>Subject</b><span class="s"></span></div></div><pre></pre></div>';
    el.querySelector(".f").textContent = `${m.from_name} <${m.from_email}>`;
    el.querySelector(".s").textContent = m.subject;
    el.querySelector("pre").textContent = m.text;
    document.body.appendChild(el);
  }, mail);
  await say(chapter, text, { who: WHO.supplier(mail.from_name), extraMs: 3000 });
  await page.evaluate(() => document.getElementById("demo-mail")?.remove());
}

async function pointAndClick(locator) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (box) {
    await page.evaluate(([x, y]) => {
      const dot = document.getElementById("demo-cursor");
      if (dot) {
        dot.style.left = `${x}px`;
        dot.style.top = `${y}px`;
      }
    }, [box.x + box.width / 2, box.y + box.height / 2]);
    await sleep(1300);
    await page.evaluate(() => {
      const dot = document.getElementById("demo-cursor");
      if (dot) dot.style.transform = "scale(.6)";
    });
  }
  await locator.click();
}

/** Type into a chat box the way a person would, send, and wait for the answer. */
async function chat(box, question, thinking) {
  pace(SPEED);
  await box.click();
  await box.pressSequentially(question, { delay: 45 });
  await sleep(600);
  await box.press("Enter");
  pace(SPEED * 2.5); // the model thinks for a while
  await sleep(1500);
  await page.getByText(thinking).first().waitFor({ state: "hidden", timeout: 120000 }).catch(() => undefined);
  await sleep(1200);
}

const approvalRow = (id) => api(`/api/approvals/${id}`);
async function rowsOf(ids) {
  const rows = [];
  for (const id of ids ?? []) rows.push(await approvalRow(id));
  return rows;
}

async function openApproval(id) {
  const row = await approvalRow(id);
  await go(`/approvals?id=${id}${row.status === "pending" ? "" : "&tab=resolved"}`);
  return row;
}
/** Click the approve button of the open approval, on screen. */
async function decide(id, chapter, text) {
  if ((await approvalRow(id)).status !== "pending") return;
  current = { chapter, text, who: WHO.person };
  await overlay();
  try {
    const button = page.getByRole("button", { name: APPROVE_BUTTON }).last();
    await button.waitFor({ state: "visible", timeout: 5000 });
    pace(SPEED);
    await pointAndClick(button);
  } catch (error) {
    log(`  approve button not found for #${id}: ${String(error).slice(0, 80)}`);
  }
  pace(WAIT_SPEED);
  for (let i = 0; i < 12; i += 1) {
    await sleep(1000);
    if ((await approvalRow(id)).status !== "pending") break;
    if (i === 11) await api(`/api/approvals/${id}/resolve`, "POST", { status: "approved", reason: "demo video" });
  }
  await read(text.split(/\s+/).length);
}
async function approveQuietly(ids) {
  for (const id of ids) {
    if ((await approvalRow(id)).status === "pending") await api(`/api/approvals/${id}/resolve`, "POST", { status: "approved", reason: "demo video" });
  }
}

/** Play scenes while a step runs; if they run out, fast-forward until the step ends. */
async function during(step, scenes, chapter, waitingText) {
  for (const scene of scenes) {
    if (step.done) break;
    await scene();
  }
  if (!step.done) {
    current = { chapter, text: waitingText, who: "" };
    await overlay();
    pace(WAIT_SPEED);
    while (!step.done) await sleep(1000);
  }
  return step.promise;
}

async function odooLogin() {
  await go(`${ODOO}/web/login`, { settle: 2500 });
  if (await page.locator("input[name=login]").count()) {
    await page.fill("input[name=login]", ODOO_LOGIN);
    await page.fill("input[name=password]", ODOO_PASSWORD);
    await page.click("button[type=submit]");
    await sleep(6000);
  }
}
async function odooForm(model, id) {
  await go(`${ODOO}/web#id=${id}&model=${model}&view_type=form`, { settle: 5000 });
  await page.locator(".o_form_view").first().waitFor({ state: "visible", timeout: 25000 }).catch(() => undefined);
  await sleep(1500);
  await overlay();
}
async function langfuseLogin() {
  await go(`${LANGFUSE}/auth/sign-in`, { settle: 3500 });
  if (await page.locator("input[name=email]").count()) {
    await page.fill("input[name=email]", LANGFUSE_USER);
    await page.fill("input[name=password]", LANGFUSE_PASSWORD);
    await page.keyboard.press("Enter");
    await sleep(6000);
  }
}

// --- the film -------------------------------------------------------------------------------------
async function main() {
  if (!PASSWORD) throw new Error("SC_UI_PASSWORD not set");
  const login = await api("/api/auth/login", "POST", { email: USER, password: PASSWORD });
  token = login.token;
  const me = await api("/api/auth/me");
  log("resetting the demo");
  const reset = await api("/api/demo/reset", "POST");
  const late = reset.records.late_order?.po_name;
  const receiptPo = reset.records.receipt_order?.po_name;
  const hidraulica = reset.records.late_order?.partner_id ?? 8;
  log(`late order ${late}, receipt order ${receiptPo}; ready ${JSON.stringify(reset.ready)}`);
  if (reset.ready.notes?.length) throw new Error(`the demo is not ready: ${reset.ready.notes.join("; ")}`);
  for (const stale of await api(`/api/approvals?status=pending&po=${late}`)) {
    await api(`/api/approvals/${stale.id}/resolve`, "POST", { status: "rejected", reason: "stale draft, retired before filming" });
    log(`retired stale approval #${stale.id} on ${late}`);
  }

  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: SIZE, recordVideo: { dir: OUT_DIR, size: SIZE }, locale: "en-US" });
  await context.addInitScript(
    ([session]) => {
      if (window.location.port === "8010") {
        window.localStorage.setItem("control-tower.session", session);
        window.localStorage.setItem("control-tower.language", "en");
      }
    },
    [JSON.stringify({ token, user: { email: me.email, name: me.name, role: me.role } })],
  );
  page = await context.newPage();
  filmStart = Date.now();
  pace(WAIT_SPEED * 4); // signing in to Odoo and Langfuse is not part of the story
  await odooLogin();
  await langfuseLogin();

  // --- opening ---------------------------------------------------------------------------------
  await go("/");
  await card("A purchasing department run by AI agents", "Live on Odoo, a real mailbox and real emails. Agents do the work. People decide. The model never writes to the ERP.", 6);
  await say("The desk", "Home: service level, late orders, approvals waiting, spend, and what the AI cost this month.");
  await spotlight(page.getByRole("region", { name: "Needs you" }), "What needs a person today");
  await say("The desk", "The Director agent coordinates six specialist agents. What needs a person is one list.");
  await clearSpotlight();

  // --- the board, explained ---------------------------------------------------------------------
  await go("/board");
  await say("The orders board", "Every purchase order in Odoo is a card. Columns follow the order's life, from proposal to invoice.");
  await spotlight(page.getByRole("region", { name: "Quotation requested" }), "Requests sent, waiting for quotes");
  await say("The orders board", "Requests for quotation wait here. A card counts the days without an answer and shows the next reminder.");
  await spotlight(page.getByRole("region", { name: "To receive" }), "Confirmed orders on their way");
  await say("The orders board", "Confirmed orders show late days, the order's age, and a predicted delay from the supplier's record.");
  await clearSpotlight();
  await page.getByLabel("Swimlanes").selectOption("priority").catch(() => undefined);
  await sleep(1500);
  await say("The orders board", "Swimlanes group the board by priority or by supplier. Problems rise to the top.");

  // --- 1. the late order -------------------------------------------------------------------------
  let step = startStep("late_order_eta");
  await during(
    step,
    [
      async () => {
        await go(`/board?po=${late}`);
        await say("1 · A late order", `${late} from Proveedor Hidraulica is past its date. Nobody had to notice: the Director agent did.`);
      },
    ],
    "1 · A late order",
    "The Supplier agent is writing to the supplier…",
  );
  let rows = await rowsOf(step.outcome?.approval_ids);
  if (rows.length) {
    await openApproval(rows[0].id);
    await spotlight(page.getByTestId("email-preview"), "Draft email, written by the Supplier agent");
    await say("1 · A late order", "The Supplier agent drafted a delivery date request from the order's own facts.", { who: WHO.agent("Supplier agent") });
    await clearSpotlight();
    await decide(rows[0].id, "1 · A late order", "A person approves, and the email leaves from the purchasing mailbox.");
  } else {
    await go(`/board?po=${late}`);
    await say("1 · A late order", "The Supplier agent already wrote asking for a firm date. A rule a person set lets date requests go alone.", { who: WHO.agent("Supplier agent") });
  }

  // live chat on the order
  try {
    await go(`/board?po=${late}`);
    current = { chapter: "Talk to the Director agent", text: "Ask about any order in plain words. It answers from Odoo, the emails and the rules.", who: "" };
    await overlay();
    await chat(page.getByLabel("Message to your AI").first(), "Why is this order late, and what have you done about it?", /is looking at the case/);
    await page.getByLabel("Message to your AI").first().scrollIntoViewIfNeeded().catch(() => undefined);
    await say("Talk to the Director agent", "A real answer, typed live: the facts of the order, and what was already done about it.");
  } catch (error) {
    log(`  order chat skipped: ${String(error).slice(0, 80)}`);
  }

  // --- 2. the supplier's reply --------------------------------------------------------------------
  step = startStep("supplier_eta_reply");
  await during(
    step,
    [
      async () => {
        await go(`/suppliers/${hidraulica}`);
        await say("While the supplier answers", "Supplier 360: scorecard, orders, prices with the supplier's rank, quote rounds and emails, on one page.");
        await scroll(520);
        await say("While the supplier answers", "Only links to emails are kept. The text of an email is never stored.");
      },
      async () => {
        await go("/autonomy");
        await say("While the supplier answers", "Autonomy: what may run without a person is a rule people set, with a 30-day preview before saving.");
      },
    ],
    "Real time",
    "The supplier's reply is travelling from its mailbox to the purchasing inbox…",
  );
  let mails = await api("/api/demo/emails");
  const etaMail = mails.find((m) => m.step === "supplier_eta_reply");
  if (etaMail) await supplierMail(etaMail, "2 · The supplier answers", "This is what the supplier wrote, from its own mailbox.");
  rows = await rowsOf(step.outcome?.approval_ids);
  const change = rows.find((r) => r.kind === "po_change") ?? rows[0];
  if (change) {
    await openApproval(change.id);
    await say("2 · The supplier answers", "The Supplier agent read the new date and proposes the change, line by line, with its confidence.", { who: WHO.agent("Supplier agent") });
    await spotlight(page.getByText("Because:", { exact: false }).first().locator("xpath=.."), "How the agent reasoned");
    await say("2 · The supplier answers", "Every proposal explains itself: the facts it used, the rule that applied, what would let it run alone.");
    await clearSpotlight();
    await decide(change.id, "2 · The supplier answers", "Odoo changes only after a person decides.");
  }
  const lateCard = (await api("/api/board")).cards.find((c) => c.po_name === late);
  if (lateCard?.case_id) {
    await go(`/cases/${lateCard.case_id}`);
    await say("History", "Every order keeps its story: the request that went out, the reply that came in, who decided what.");
    await scroll(420);
    await say("History", "Each email is a link that opens it in Outlook. Outgoing and incoming, in order.");
  }
  if (lateCard) {
    await odooForm("purchase.order", lateCard.po_id);
    await say("In Odoo", `${late} in Odoo: the delivery date moved, and the agent left an audit note on the order.`, { who: WHO.odoo });
    await scroll(600, { within: ".o_content" });
    await scroll(600);
    await say("In Odoo", "Odoo stays the system of record. The agents work through a bot user with limited rights.", { who: WHO.odoo });
  }

  // --- 3. the risk radar and the quote round ---------------------------------------------------------
  await go("/risk");
  await say("3 · A stockout risk", "The risk radar: the odds of running out in 30 and 60 days per product, with the cash at stake.");
  step = startStep("risk_quote_round");
  await during(step, [async () => say("3 · A stockout risk", "For a product at risk, the Sourcing agent asks every supplier who lists it for a quote.")], "3 · A stockout risk", "The Sourcing agent is preparing the requests…");
  const records3 = step.view?.records ?? {};
  const rfq = records3.supplier_rfq;
  const rfqNames = records3.rfq_names ?? [];
  rows = await rowsOf(step.outcome?.approval_ids);
  if (rows.length) {
    const mine = rows.find((r) => r.po_name === rfq) ?? rows[0];
    await openApproval(mine.id);
    await spotlight(page.getByTestId("email-preview"), "Draft email, written by the Supplier agent");
    await say("3 · A stockout risk", `One request for quotation per supplier: ${rfqNames.length} this time. Each is drafted from the order's lines.`, { who: WHO.agent("Supplier agent") });
    await clearSpotlight();
    await decide(mine.id, "3 · A stockout risk", "Approved. The other requests are approved the same way.");
    await approveQuietly(rows.filter((r) => r.id !== mine.id).map((r) => r.id));
  }
  const rfqCard = rfq ? (await api("/api/board")).cards.find((c) => c.po_name === rfq) : null;
  if (rfqCard) {
    await go(`${ODOO}/report/html/purchase.report_purchasequotation/${rfqCard.po_id}`, { settle: 3500 });
    await say("The PDF", "The request the supplier receives as a PDF: printed by Odoo, attached by the agent.", { who: WHO.odoo });
  }

  // --- 4. three quotes, and a counter-offer ------------------------------------------------------------
  step = startStep("supplier_quote", { approve: true, leave: ["negotiation_offer"] });
  await during(
    step,
    [
      async () => {
        await go("/planning");
        await say("While the suppliers quote", "Planning: every morning the Planning agent proposes what to buy from two years of demand.");
      },
      async () => {
        await go("/playbooks");
        await say("While the suppliers quote", "Playbooks: multi-step plans that run for days. Remind, ask for a date, escalate, find another source.");
      },
      async () => {
        await go("/suppliers");
        await say("While the suppliers quote", "Suppliers are scored weekly on what they did: on time and in full, lead time, replies, prices.");
      },
    ],
    "Real time",
    "The suppliers are answering from their mailboxes…",
  );
  mails = await api("/api/demo/emails");
  const quotes = mails.filter((m) => m.step === "supplier_quote");
  const quoteLine = (name) =>
    /alterna/i.test(name) ? "Hidráulica Alterna: at its list price, and it delivers in 12 days." : /importadora/i.test(name) ? "Importadora del Sur: the cheapest quote, and by far the slowest at 55 days." : "Proveedor Hidraulica quotes 12% above its own list price, 20 days.";
  for (const mail of quotes) await supplierMail(mail, "4 · The quotes arrive", quoteLine(mail.from_name));
  rows = await rowsOf(step.outcome?.approval_ids);
  const quoted = rows.find((r) => r.kind === "po_change" && r.po_name === rfq);
  if (quoted) {
    await openApproval(quoted.id);
    await say("4 · The quotes arrive", "The Supplier agent read each quote and recorded price and lead time on the request, line by line.", { who: WHO.agent("Supplier agent") });
  }
  const offer = rows.find((r) => r.kind === "negotiation_offer");
  if (offer) {
    await openApproval(offer.id);
    await say("4 · A counter-offer", "The Sourcing agent proposes a counter-offer: target, floor and the buyer's cap, with the evidence.", { who: WHO.agent("Sourcing agent") });
    await decide(offer.id, "4 · A counter-offer", "A counter-offer is always a person's decision. No rule can automate it.");
  }

  // --- 5. the acceptance, and how the supplier is chosen ----------------------------------------------
  step = startStep("award", { approve: true, leave: ["award"] });
  await during(
    step,
    [
      async () => {
        await go("/assistant");
        current = { chapter: "Ask the Director agent", text: "Ask about the whole department. Answers cite the records they rest on.", who: "" };
        await overlay();
        try {
          await chat(page.getByLabel("Message to your AI").last(), "Which orders are late, and what is being done about them?", /is reading the desk/);
          await say("Ask the Director agent", "Typed live. Every reference under the answer opens the order, the approval or the rule it cites.");
        } catch (error) {
          log(`  assistant scene skipped: ${String(error).slice(0, 80)}`);
        }
      },
      async () => {
        await go("/runs");
        await say("While the supplier decides", "Runs: every agent run with its model, tokens, cost in dollars, duration and a link to its trace.");
      },
    ],
    "Real time",
    "The supplier is answering the counter-offer…",
  );
  mails = await api("/api/demo/emails");
  const acceptance = mails.find((m) => m.step === "award");
  if (acceptance) await supplierMail(acceptance, "5 · The supplier accepts", "Proveedor Hidraulica accepts the counter-offer.");
  rows = await rowsOf(step.outcome?.approval_ids);
  const award = rows.find((r) => r.kind === "award");
  if (award) {
    await openApproval(award.id);
    await say("5 · Choosing the supplier", "How is the supplier chosen? The Sourcing agent compares every quote on the same terms.", { who: WHO.agent("Sourcing agent") });
    await spotlight(page.getByRole("table").first(), "Landed cost · lead time · supplier score · why");
    await say("5 · Choosing the supplier", "Landed cost includes freight. Lead time comes from the quote. The score is the supplier's measured record.");
    const ranked = [...(award.payload?.comparison?.quotes ?? [])].sort((a, b) => (a.rank ?? 9) - (b.rank ?? 9));
    const [winner, runner] = ranked;
    const points = (q) => Math.round((q?.composite ?? 0) * 100);
    await say("5 · Choosing the supplier", "Weights: 60% price, 20% lead time, 20% score. Each quote gets one composite number, and they are ranked.");
    if (winner && runner)
      await say(
        "5 · Choosing the supplier",
        `Here ${winner.partner_name} ranks first with ${points(winner)} points: ${winner.lead_days} days, score ${Math.round(winner.score ?? 0)}. ${runner.partner_name} follows with ${points(runner)}: ${runner.lead_days} days, score ${Math.round(runner.score ?? 0)}.`,
      );
    await clearSpotlight();
    await scroll(300);
    await say("5 · Choosing the supplier", "The recommendation is written out with its reasons. The buyer can still award line by line.");
    await decide(award.id, "5 · Choosing the supplier", "A person awards. Odoo confirms the order; the others get a courteous decline.");
    pace(WAIT_SPEED);
    await sleep(15000);
    for (const name of rfqNames) await approveQuietly((await api(`/api/approvals?status=pending&po=${name}`)).map((a) => a.id));
    await sleep(8000);
  }
  const afterAward = (await api("/api/board")).cards.filter((c) => rfqNames.includes(c.po_name));
  const awardedCard = afterAward.find((c) => c.state === "purchase") ?? afterAward.find((c) => c.po_name === rfq);
  if (awardedCard) {
    await go(`${ODOO}/report/html/purchase.report_purchaseorder/${awardedCard.po_id}`, { settle: 3500 });
    await say("The PDF", "The purchase order as Odoo prints it, for the supplier that won. The agent sends it as a PDF.", { who: WHO.odoo });
  }

  // --- the thinking, in Langfuse -------------------------------------------------------------------------
  try {
    const runs = await api("/api/runs?limit=80");
    // the run that read the supplier's quote: its model call shows the email going in and
    // structured prices coming out
    const reads = runs.filter((r) => r.trace_url && r.agent === "supplier_comms" && r.llm_calls > 0 && /change\(s\) applied/.test(r.summary ?? ""));
    const traced = reads.find((r) => r.po_name === rfq) ?? reads[0] ?? runs.find((r) => r.trace_url && r.llm_calls > 0);
    if (traced) {
      await go(traced.trace_url.replace(/^https?:\/\/[^/]+/, LANGFUSE), { settle: 4000 });
      // the trace page compiles on first use: wait for its tree
      await page.getByText(/chat deepseek/).first().waitFor({ state: "visible", timeout: 90000 }).catch(() => undefined);
      await sleep(1500);
      await overlay();
      await say("The thinking", "Every agent run is traced in Langfuse, step by step, with the time and the cost of each model call.", { who: WHO.langfuse });
      // our own generation span carries the prompt and the answer; it is the parent of the raw call
      const extract = page.getByText(/^supplier_comms\.extract/).first();
      const generation = (await extract.count()) ? extract : page.getByText(/^(supplier_comms|sourcing|inventory_planning|logistics|invoice_match|director)\.(?!task|workflow)[a-z_.0-9]+$/).first();
      if (await generation.count()) {
        pace(SPEED);
        await pointAndClick(generation).catch(() => undefined);
        await sleep(3000);
        // "JSON" unfolds the call: every message that went in, everything that came out
        const unfold = page.getByText("JSON", { exact: true }).first();
        if (await unfold.count()) await pointAndClick(unfold).catch(() => undefined);
        await sleep(2500);
        await overlay();
        await say("The thinking", "One model call, opened: the Supplier agent reading a supplier's email. First the instructions it was given.", { who: WHO.langfuse });
        await page.mouse.move(1150, 450);
        await page.mouse.wheel(0, 520);
        await sleep(1200);
        await say("The thinking", "Then the facts: the order from Odoo, line by line, and the supplier's email exactly as it arrived.", { who: WHO.langfuse });
        await page.getByText("Output", { exact: true }).first().scrollIntoViewIfNeeded().catch(() => undefined);
        await page.mouse.wheel(0, 260);
        await sleep(1200);
        await overlay();
        await say("The thinking", "And the answer: the model's reasoning, then structured data. Price, lead time, and a confidence the code checks.", { who: WHO.langfuse });
        await page.mouse.wheel(0, 600);
        await sleep(1200);
        await read(8);
      }
      await say("The thinking", "The model proposes. Deterministic code checks the answer and does the writing, behind an approval or a rule.", { who: WHO.langfuse });
    } else log("  no trace url on the runs");
  } catch (error) {
    log(`  langfuse scene skipped: ${String(error).slice(0, 100)}`);
  }

  // --- 6. the short receipt ---------------------------------------------------------------------------
  step = startStep("short_receipt");
  await during(
    step,
    [
      async () => {
        await go(`/board?po=${receiptPo}`);
        await say("6 · A short receipt", "The warehouse receives an order in Odoo: 18 units arrive, 20 were ordered.");
      },
    ],
    "6 · A short receipt",
    "The Logistics agent is reconciling the receipt…",
  );
  rows = await rowsOf(step.outcome?.approval_ids);
  if (rows.length) {
    await openApproval(rows[0].id);
    await spotlight(page.getByTestId("email-preview"), "Draft email, written by the Logistics agent");
    await say("6 · A short receipt", "The Logistics agent reconciled receipt against order and drafted the discrepancy report.", { who: WHO.agent("Logistics agent") });
    await clearSpotlight();
    await decide(rows[0].id, "6 · A short receipt", "Approved, and the supplier is told what is missing.");
    await approveQuietly(rows.slice(1).map((r) => r.id));
  }

  // --- 7. the invoice -----------------------------------------------------------------------------------
  step = startStep("invoice_variance");
  await during(step, [async () => say("7 · An invoice with a variance", "Accounting types the supplier's bill in Odoo. One line is 3% above the order.")], "7 · An invoice with a variance", "The Invoice agent is matching the bill…");
  rows = await rowsOf(step.outcome?.approval_ids);
  if (rows.length) {
    await openApproval(rows[0].id);
    await say("7 · An invoice with a variance", "The Invoice agent matched bill, order and receipt line by line, and found the price difference.", { who: WHO.agent("Invoice agent") });
    await decide(rows[0].id, "7 · An invoice with a variance", "A person decides. Nothing is ever posted by the agents.");
    await approveQuietly(rows.slice(1).map((r) => r.id));
  }
  const billId = step.view?.records?.bill?.move_id;
  if (billId) {
    await odooForm("account.move", billId);
    await say("In Odoo", "The vendor bill in Odoo, still a draft, with the agent's check recorded on it.", { who: WHO.odoo });
  }

  // --- 8. the briefing, and the numbers ------------------------------------------------------------------
  step = startStep("briefing");
  await during(step, [async () => say("8 · The next morning", "At 07:30 the Director agent writes the briefing from the facts of the day.")], "8 · The next morning", "The Director agent is writing the briefing…");
  await go("/briefing");
  await say("8 · The next morning", "What happened, what ran alone, what needs a decision, and the top risks. Every line opens its record.");
  await scroll(460);
  await read(8);
  await go("/ai");
  await say("The numbers", "AI performance: automation rate by decision, how fast people answer, forecast error, and cost per case.");
  await go("/");
  await say("The desk", "One day of purchasing: eight situations handled, every decision explained, for a few cents of AI.");
  await card("Every email was real. Every Odoo record is real.", "Agents do the work. Approvals and rules people set keep them in check.", 6);

  const video = page.video();
  await context.close();
  await browser.close();
  const raw = resolve(OUT_DIR, "demo-raw.webm");
  renameSync(await video.path(), raw);
  const seconds = (Date.now() - filmStart) / 1000;
  timeline.push({ at: seconds, speed: 0 });
  writeFileSync(resolve(OUT_DIR, "demo-timeline.json"), JSON.stringify(timeline, null, 1));
  log(`raw take: ${raw} (${Math.floor(seconds / 60)}m${String(Math.round(seconds % 60)).padStart(2, "0")}s)`);
  cut(raw, timeline, resolve(OUT_DIR, "demo.mp4"));
}

/** Re-time the take with ffmpeg: each stretch of the timeline plays at its own speed.
 *  One stretch at a time, then a stream-copy join: a single filter graph over the whole
 *  take decodes it once per stretch and runs a laptop out of memory. */
function cut(raw, marks, target) {
  let ffmpeg = process.env.FFMPEG_PATH;
  if (!ffmpeg) {
    try {
      ffmpeg = createRequire(import.meta.url)("ffmpeg-static");
    } catch {
      ffmpeg = "ffmpeg";
    }
  }
  const parts = [];
  for (let i = 0; i + 1 < marks.length; i += 1) {
    const [a, b, speed] = [marks[i].at, marks[i + 1].at, marks[i].speed];
    if (b - a < 0.2 || !speed) continue;
    parts.push({ a, b, speed });
  }
  const dir = resolve(OUT_DIR, "parts");
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const names = [];
  for (const [i, p] of parts.entries()) {
    const name = `part_${String(i).padStart(3, "0")}.mp4`;
    const result = spawnSync(
      ffmpeg,
      ["-v", "error", "-y", "-ss", p.a.toFixed(2), "-t", (p.b - p.a).toFixed(2), "-i", raw, "-vf", `setpts=(PTS-STARTPTS)/${p.speed},fps=30`, "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-threads", "2", resolve(dir, name)],
      { stdio: "inherit" },
    );
    if (result.status !== 0) {
      log(`ffmpeg did not run (${ffmpeg}); the raw take and demo-timeline.json are in ${OUT_DIR}`);
      return;
    }
    names.push(name);
  }
  writeFileSync(resolve(dir, "list.txt"), names.map((n) => `file '${n}'`).join("\n"));
  const joined = spawnSync(ffmpeg, ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", resolve(dir, "list.txt"), "-c", "copy", "-movflags", "+faststart", target], { stdio: "inherit" });
  if (joined.status === 0 && existsSync(target)) {
    rmSync(dir, { recursive: true, force: true });
    const length = parts.reduce((sum, p) => sum + (p.b - p.a) / p.speed, 0);
    log(`video: ${target} (${Math.floor(length / 60)}m${String(Math.round(length % 60)).padStart(2, "0")}s)`);
  } else log(`ffmpeg could not join the parts in ${dir}`);
}

if (args.includes("--recut")) {
  // the take was filmed for 2x; another --speed scales every stretch by the same ratio
  const marks = JSON.parse(readFileSync(resolve(OUT_DIR, "demo-timeline.json"), "utf8")).map((m) => ({ ...m, speed: (m.speed * SPEED) / 2 }));
  cut(resolve(OUT_DIR, "demo-raw.webm"), marks, resolve(OUT_DIR, "demo.mp4"));
} else {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
