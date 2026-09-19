/**
 * Record the demo day as a captioned video, on the live stack.
 *
 *   SC_UI_PASSWORD=... node scripts/demo-video.mjs [--lang en|es] [--out ../../demo-video]
 *
 * The scenario is the director's own (POST /api/demo/next, see docs/demo.md): each step runs
 * through the API while the browser tours the screens that matter, so the time a step waits
 * for a real email is spent showing the product. Approvals a step raises are decided on
 * screen, in the inbox, wherever the script allows it; no voice, the captions tell the story.
 * The result is a .webm (Playwright's recorder); convert it with ffmpeg for an .mp4.
 */
import { mkdirSync, renameSync } from "node:fs";
import { resolve } from "node:path";

import { chromium } from "@playwright/test";

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
};
const LANG = flag("lang", "en") === "es" ? "es" : "en";
const BASE = (process.env.SC_DIRECTOR_URL ?? "http://localhost:8010").replace(/\/$/, "");
const USER = process.env.SC_DEMO_USER ?? "admin@scai.dev";
const PASSWORD = process.env.SC_UI_PASSWORD;
const OUT_DIR = resolve(flag("out", "../../demo-video"));
const SIZE = { width: 1600, height: 900 };
const APPROVE_BUTTON = /^(Approve|Aprobar)( \d+ (line|línea)\(s\)| with edits| con cambios)?$|^(Award|Adjudicar|Send the counter-offer|Enviar la contraoferta)$/;

if (!PASSWORD) {
  console.error("SC_UI_PASSWORD not set");
  process.exit(2);
}

// --- what the captions say ---------------------------------------------------------------------
const T = {
  en: {
    intro_title: "A purchasing department run by AI agents",
    intro_text: "Live on Odoo, a real mailbox and real emails. People decide; the model never writes.",
    home: ["The desk", "Service level, late orders, what needs a person, what ran alone, and what the AI cost this month."],
    late: ["1 · A late order", "An order from Proveedor Hidraulica is past its date. Nobody had to notice: the department did."],
    late_done: ["1 · A late order", "It already wrote to the supplier asking for a firm date, in the supplier's language. A rule a person set let that email go alone."],
    supplier360: ["While the supplier answers…", "Everything about a supplier on one page: scorecard, orders, prices with its rank, quote rounds, emails. Only links are kept, never the text of an email."],
    autonomy: ["While the supplier answers…", "What may run without a person is a rule people set, with a preview of what it would have done in the last 30 days."],
    waiting_mail: ["Real time", "The supplier's email is travelling from its own mailbox to the purchasing inbox…"],
    eta_change: ["2 · The supplier answers", "The agent read the new date in the email and proposes to move the order. Odoo only changes after a person decides."],
    eta_moved: ["2 · The supplier answers", "Approved: the date moved in Odoo and the order is no longer late. The email is one click away, in Outlook."],
    eta_review: ["2 · The supplier answers", "When the agent is not sure of what it read, it says so and leaves the lines for a person to review. Nothing moves on a guess."],
    risk: ["3 · A stockout risk", "The risk radar: the odds of running out in 30 and 60 days, per product, with the cash at stake."],
    risk_running: ["3 · A stockout risk", "For the riskiest product with nothing on order, the sourcing agent asks every supplier who lists it for a quote."],
    rfq: ["3 · A stockout risk", "One request for quotation per supplier, written by the agent with the order's own lines. A person reads it and approves."],
    round_board: ["3 · A stockout risk", "The requests sit on the board, grouped as alternatives in Odoo, each with its own history."],
    planning: ["While the supplier quotes…", "Every morning the planner proposes what to buy from two years of demand: forecasts chosen by backtest, with the reason for every line."],
    suppliers: ["While the supplier quotes…", "Suppliers are scored every week on what they actually did: on-time and in-full deliveries, lead time, answers to email, price stability."],
    runs: ["While the supplier decides…", "Every agent run is on record: what it did, how long it took, what it cost, and the full trace."],
    cases: ["While the supplier decides…", "Every order has a history a person can read: who did what, which email, which decision, by whom."],
    playbooks: ["While the supplier quotes…", "Multi-step plans that run for days: remind, ask for a date, escalate, look for another source."],
    quote: ["4 · A quote above the list", "The supplier quoted 12% above its own list price. The quote is recorded on the request, line by line."],
    offer: ["4 · A counter-offer", "The sourcing agent proposes a counter-offer inside the buyer's cap, with the evidence behind the target. It goes out only with a person's approval."],
    assistant: ["While the supplier decides…", "Ask the department anything. Answers cite the records they rest on; an instruction becomes a plan a person confirms once."],
    assistant_question: "Which orders are late and what is being done about them?",
    award: ["5 · The award", "The supplier accepted. The comparison weighs landed cost, lead time and the supplier's score, recommends, and a person awards."],
    awarded_board: ["5 · The award", "Odoo confirmed the order and the purchase order went to the supplier as a PDF."],
    receipt_running: ["6 · A short receipt", "The warehouse receives an order: 18 units arrive, 20 were ordered."],
    receipt: ["6 · A short receipt", "The logistics agent reconciled the receipt against the order and drafted the discrepancy report for the supplier."],
    bill_running: ["7 · An invoice with a variance", "Accounting types the supplier's bill. One line is 3% above the order."],
    bill: ["7 · An invoice with a variance", "The matching agent checked the bill against the order and the receipt, line by line, before anything is posted."],
    briefing_running: ["8 · The next morning", "At 07:30 the director writes the briefing from the facts of the day."],
    briefing: ["8 · The next morning", "What happened, what ran alone, what needs a decision and the top risks. Every line opens the record behind it."],
    ai: ["The numbers", "Automation rate by kind of decision, how fast people answer, forecast error, and the cost per case: fractions of a cent."],
    outro_title: "Every email was real. Every Odoo record is real.",
    outro_text: "Eight steps on the live stack, in real time. Agents do the work; approvals and rules people set keep them in check.",
    approved: "Approved",
  },
  es: {
    intro_title: "Un departamento de compras operado por agentes de IA",
    intro_text: "En vivo sobre Odoo, un buzón real y correos reales. Las personas deciden; el modelo nunca escribe.",
    home: ["El escritorio", "Nivel de servicio, órdenes atrasadas, lo que necesita a una persona, lo que corrió solo y lo que costó la IA este mes."],
    late: ["1 · Una orden atrasada", "Una orden de Proveedor Hidraulica pasó su fecha. Nadie tuvo que darse cuenta: el departamento lo hizo."],
    late_done: ["1 · Una orden atrasada", "Ya le escribió al proveedor pidiendo una fecha firme, en su idioma. Una regla definida por una persona dejó salir ese correo solo."],
    supplier360: ["Mientras el proveedor responde…", "Todo sobre un proveedor en una página: evaluación, órdenes, precios con su ranking, rondas de cotización, correos. Solo se guardan enlaces, nunca el texto de un correo."],
    autonomy: ["Mientras el proveedor responde…", "Qué puede correr sin una persona es una regla que definen las personas, con una vista previa de lo que habría hecho en los últimos 30 días."],
    waiting_mail: ["Tiempo real", "El correo del proveedor viaja desde su propio buzón hasta la bandeja de compras…"],
    eta_change: ["2 · El proveedor responde", "El agente leyó la nueva fecha en el correo y propone mover la orden. Odoo solo cambia cuando una persona decide."],
    eta_moved: ["2 · El proveedor responde", "Aprobado: la fecha cambió en Odoo y la orden ya no está atrasada. El correo está a un clic, en Outlook."],
    eta_review: ["2 · El proveedor responde", "Cuando el agente no está seguro de lo que leyó, lo dice y deja las líneas para que una persona las revise. Nada se mueve por una suposición."],
    risk: ["3 · Un riesgo de quiebre", "El radar de riesgo: la probabilidad de quedarse sin stock a 30 y 60 días, por producto, con el dinero en juego."],
    risk_running: ["3 · Un riesgo de quiebre", "Para el producto más expuesto y sin nada en camino, el agente de abastecimiento pide cotización a cada proveedor que lo ofrece."],
    rfq: ["3 · Un riesgo de quiebre", "Una solicitud de cotización por proveedor, redactada por el agente con las líneas de la orden. Una persona la lee y aprueba."],
    round_board: ["3 · Un riesgo de quiebre", "Las solicitudes quedan en el tablero, agrupadas como alternativas en Odoo, cada una con su historia."],
    planning: ["Mientras el proveedor cotiza…", "Cada mañana el planificador propone qué comprar a partir de dos años de demanda: pronósticos elegidos por backtest, con la razón de cada línea."],
    suppliers: ["Mientras el proveedor cotiza…", "Los proveedores se evalúan cada semana por lo que realmente hicieron: entregas a tiempo y completas, plazo, respuesta a correos, estabilidad de precios."],
    runs: ["Mientras el proveedor decide…", "Cada ejecución de un agente queda registrada: qué hizo, cuánto tardó, cuánto costó y la traza completa."],
    cases: ["Mientras el proveedor decide…", "Cada orden tiene una historia legible: quién hizo qué, qué correo, qué decisión y de quién."],
    playbooks: ["Mientras el proveedor cotiza…", "Planes de varios pasos que duran días: recordar, pedir fecha, escalar, buscar otra fuente."],
    quote: ["4 · Una cotización sobre la lista", "El proveedor cotizó 12% por encima de su propia lista. La cotización queda registrada en la solicitud, línea por línea."],
    offer: ["4 · Una contraoferta", "El agente propone una contraoferta dentro del tope del comprador, con la evidencia del precio objetivo. Solo sale con la aprobación de una persona."],
    assistant: ["Mientras el proveedor decide…", "Pregúntale lo que sea al departamento. Las respuestas citan los registros; una instrucción se vuelve un plan que una persona confirma una vez."],
    assistant_question: "¿Qué órdenes están atrasadas y qué se está haciendo al respecto?",
    award: ["5 · La adjudicación", "El proveedor aceptó. La comparación pondera costo puesto en almacén, plazo y evaluación del proveedor, recomienda, y una persona adjudica."],
    awarded_board: ["5 · La adjudicación", "Odoo confirmó la orden y la orden de compra salió al proveedor en PDF."],
    receipt_running: ["6 · Una recepción incompleta", "El almacén recibe una orden: llegan 18 unidades, se pidieron 20."],
    receipt: ["6 · Una recepción incompleta", "El agente de logística concilió la recepción con la orden y redactó el reporte de discrepancia para el proveedor."],
    bill_running: ["7 · Una factura con diferencia", "Contabilidad registra la factura del proveedor. Una línea está 3% por encima de la orden."],
    bill: ["7 · Una factura con diferencia", "El agente de conciliación revisó la factura contra la orden y la recepción, línea por línea, antes de contabilizar nada."],
    briefing_running: ["8 · A la mañana siguiente", "A las 07:30 el director escribe el informe con los hechos del día."],
    briefing: ["8 · A la mañana siguiente", "Qué pasó, qué corrió solo, qué necesita una decisión y los principales riesgos. Cada línea abre el registro que la respalda."],
    ai: ["Los números", "Tasa de automatización por tipo de decisión, qué tan rápido responden las personas, error de pronóstico y costo por caso: fracciones de centavo."],
    outro_title: "Cada correo fue real. Cada registro de Odoo es real.",
    outro_text: "Ocho pasos sobre el sistema en vivo, en tiempo real. Los agentes hacen el trabajo; las aprobaciones y las reglas de las personas los mantienen a raya.",
    approved: "Aprobado",
  },
}[LANG];

// --- the director's API ---------------------------------------------------------------------------
let token = "";
async function api(path, method = "GET", body) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const log = (...parts) => console.log(new Date().toISOString().slice(11, 19), ...parts);

/** Run one step, asking again while it waits for the mailbox; resolves with its outcome. */
function startStep(key, approve) {
  const state = { done: false, outcome: null, view: null };
  state.promise = (async () => {
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const view = await api("/api/demo/next", "POST", { approve, step: key });
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
let current = ["", ""];

async function overlay() {
  await page.evaluate(
    ([title, text]) => {
      let bar = document.getElementById("demo-caption");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "demo-caption";
        bar.style.cssText =
          "position:fixed;left:0;right:0;bottom:0;z-index:2147483646;padding:18px 40px 22px;color:#fff;" +
          "background:linear-gradient(180deg,rgba(10,14,24,0.0) 0%,rgba(10,14,24,0.92) 28%);font-family:Inter,Segoe UI,system-ui,sans-serif;pointer-events:none";
        bar.innerHTML = '<div id="demo-caption-title" style="font-size:15px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#7dd3fc;margin-top:26px"></div><div id="demo-caption-text" style="font-size:24px;line-height:1.35;font-weight:500;max-width:1400px;margin-top:4px"></div>';
        document.body.appendChild(bar);
      }
      document.getElementById("demo-caption-title").textContent = title;
      document.getElementById("demo-caption-text").textContent = text;
      if (!document.getElementById("demo-cursor")) {
        const dot = document.createElement("div");
        dot.id = "demo-cursor";
        dot.style.cssText =
          "position:fixed;left:-40px;top:-40px;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;z-index:2147483647;" +
          "background:rgba(56,189,248,.35);border:2px solid #38bdf8;transition:left .7s ease,top .7s ease,transform .15s;pointer-events:none";
        document.body.appendChild(dot);
      }
    },
    current,
  );
}

async function caption(pair) {
  current = pair;
  await overlay();
}

async function show(path, pair, ms = 6000, { scroll = 0 } = {}) {
  // a busy director can be slow to answer: try again rather than lose the take
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded", timeout: 45000 });
      break;
    } catch (error) {
      log(`  ${path} did not load (attempt ${attempt + 1}): ${String(error).slice(0, 60)}`);
    }
  }
  await sleep(900);
  // the page's own queries: no spinner left on screen
  await page.waitForFunction(() => !/Loading|Cargando/.test(document.querySelector("main")?.innerText ?? ""), null, { timeout: 20000 }).catch(() => undefined);
  await sleep(500);
  await caption(pair);
  if (scroll) {
    await sleep(ms * 0.4);
    await page.evaluate((top) => window.scrollBy({ top, behavior: "smooth" }), scroll);
    await sleep(ms * 0.6);
  } else {
    await sleep(ms);
  }
}

async function card(title, text, ms) {
  await page.evaluate(
    ([heading, body]) => {
      const el = document.createElement("div");
      el.id = "demo-card";
      el.style.cssText =
        "position:fixed;inset:0;z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;" +
        "background:#0b1020;color:#fff;font-family:Inter,Segoe UI,system-ui,sans-serif;text-align:center;padding:0 140px";
      el.innerHTML = `<div style="font-size:52px;font-weight:700;line-height:1.15"></div><div style="font-size:24px;color:#cbd5e1;line-height:1.4;max-width:1100px"></div>`;
      el.children[0].textContent = heading;
      el.children[1].textContent = body;
      document.body.appendChild(el);
    },
    [title, text],
  );
  await sleep(ms);
  await page.evaluate(() => document.getElementById("demo-card")?.remove());
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
    await sleep(1100);
    await page.evaluate(() => {
      const dot = document.getElementById("demo-cursor");
      if (dot) dot.style.transform = "scale(.6)";
    });
  }
  await locator.click();
}

/** Show an approval in the inbox; decide it on screen when it is still pending. */
async function showApproval(id, pair, { ms = 7000, scroll = 260 } = {}) {
  const before = await api(`/api/approvals/${id}`);
  await show(`/approvals?id=${id}${before.status === "pending" ? "" : "&tab=resolved"}`, pair, ms, { scroll });
  if (before.status !== "pending") return;
  try {
    const button = page.getByRole("button", { name: APPROVE_BUTTON }).last();
    await button.waitFor({ state: "visible", timeout: 5000 });
    await pointAndClick(button);
  } catch (error) {
    log(`  approve button not found for #${id}: ${String(error).slice(0, 80)}`);
  }
  for (let i = 0; i < 10; i += 1) {
    await sleep(1000);
    if ((await api(`/api/approvals/${id}`)).status !== "pending") break;
    if (i === 9) await api(`/api/approvals/${id}/resolve`, "POST", { status: "approved", reason: "demo video" });
  }
  await caption([pair[0], `${T.approved}. ${pair[1]}`]);
  await sleep(1800);
}

async function approveQuietly(ids) {
  for (const id of ids) {
    const row = await api(`/api/approvals/${id}`);
    if (row.status === "pending") await api(`/api/approvals/${id}/resolve`, "POST", { status: "approved", reason: "demo video" });
  }
}

async function kindsOf(ids) {
  const rows = [];
  for (const id of ids) rows.push(await api(`/api/approvals/${id}`));
  return rows;
}

/** Play the filler scenes while a step runs, then hold the last screen until it ends. */
async function during(step, fillers, waitingPair) {
  for (const filler of fillers) {
    if (step.done) break;
    await filler();
  }
  if (!step.done) {
    await caption(waitingPair);
    while (!step.done) await sleep(1000);
  }
  return step.promise;
}

// --- the film -------------------------------------------------------------------------------------
async function main() {
  const login = await api("/api/auth/login", "POST", { email: USER, password: PASSWORD });
  token = login.token;
  const me = await api("/api/auth/me");
  log("resetting the demo");
  const reset = await api("/api/demo/reset", "POST");
  const late = reset.records.late_order?.po_name;
  const receiptPo = reset.records.receipt_order?.po_name;
  log(`late order ${late}, receipt order ${receiptPo}; ready ${JSON.stringify(reset.ready)}`);
  if (reset.ready.notes?.length) throw new Error(`the demo is not ready: ${reset.ready.notes.join("; ")}`);
  for (const stale of await api(`/api/approvals?status=pending&po=${late}`)) {
    await api(`/api/approvals/${stale.id}/resolve`, "POST", { status: "rejected", reason: "stale draft, retired before filming" });
    log(`retired stale approval #${stale.id} on ${late}`);
  }

  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: SIZE, recordVideo: { dir: OUT_DIR, size: SIZE }, locale: LANG === "es" ? "es-PE" : "en-US" });
  await context.addInitScript(
    ([session, language]) => {
      window.localStorage.setItem("control-tower.session", session);
      window.localStorage.setItem("control-tower.language", language);
    },
    [JSON.stringify({ token, user: { email: me.email, name: me.name, role: me.role } }), LANG],
  );
  page = await context.newPage();
  const started = Date.now();

  // opening
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await sleep(1200);
  await card(T.intro_title, T.intro_text, 5500);
  await caption(T.home);
  await sleep(6500);

  // 1. the late order
  let step = startStep("late_order_eta", false);
  await during(step, [() => show(`/board?po=${late}`, T.late, 8000)], T.late);
  let ids = step.outcome?.approval_ids ?? [];
  if (ids.length) await showApproval(ids[0], T.late_done);
  else await show(`/board?po=${late}`, T.late_done, 7500, { scroll: 0 });

  // 2. the supplier's reply
  step = startStep("supplier_eta_reply", false);
  await during(
    step,
    [() => show(`/suppliers/${reset.records.late_order?.partner_id ?? 8}`, T.supplier360, 13000, { scroll: 520 }), () => show("/autonomy", T.autonomy, 11000, { scroll: 380 }), () => show(`/board?po=${late}`, T.waiting_mail, 6000)],
    T.waiting_mail,
  );
  let rows = await kindsOf(step.outcome?.approval_ids ?? []);
  const change = rows.find((r) => r.kind === "po_change") ?? rows[0];
  if (change) {
    await showApproval(change.id, T.eta_change, { ms: 8000 });
    await sleep(2500);
  }
  const lateCard = (await api("/api/board")).cards.find((card) => card.po_name === late);
  await show(`/board?po=${late}`, lateCard && lateCard.delivery !== "late" ? T.eta_moved : T.eta_review, 7500);

  // 3. the risk radar and the quote round
  await show("/risk", T.risk, 8000, { scroll: 240 });
  step = startStep("risk_quote_round", false);
  await during(step, [async () => { await caption(T.risk_running); await sleep(6000); }], T.risk_running);
  ids = step.outcome?.approval_ids ?? [];
  const rfq = step.view?.records?.supplier_rfq;
  if (ids.length) {
    const rows = await kindsOf(ids);
    const mine = rows.find((r) => r.po_name === rfq) ?? rows[0];
    await showApproval(mine.id, T.rfq, { ms: 8000 });
    await approveQuietly(ids.filter((id) => id !== mine.id));
  }
  if (rfq) await show(`/board?po=${rfq}`, T.round_board, 7000);

  // 4. the quote and the counter-offer
  step = startStep("supplier_quote", true);
  await during(step, [() => show("/planning", T.planning, 12000, { scroll: 420 }), () => show("/playbooks", T.playbooks, 10000, { scroll: 300 }), () => show("/suppliers", T.suppliers, 10000, { scroll: 260 }), () => show(`/board?po=${rfq}`, T.waiting_mail, 8000)], T.waiting_mail);
  rows = await kindsOf(step.outcome?.approval_ids ?? []);
  const quoted = rows.find((r) => r.kind === "po_change");
  const offer = rows.find((r) => r.kind === "negotiation_offer");
  if (quoted) await showApproval(quoted.id, T.quote, { ms: 7500 });
  if (offer) await showApproval(offer.id, T.offer, { ms: 10000, scroll: 320 });

  // 5. the acceptance and the award
  step = startStep("award", true);
  await during(
    step,
    [
      async () => {
        await show("/assistant", T.assistant, 3500);
        try {
          const box = page.getByRole("textbox").last();
          await box.click();
          await box.pressSequentially(T.assistant_question, { delay: 28 });
          await sleep(500);
          await box.press("Enter");
          await page.locator('[aria-label="Sources"], [aria-label="Fuentes"]').last().waitFor({ state: "visible", timeout: 75000 }).catch(() => undefined);
          await sleep(9000);
        } catch (error) {
          log(`  assistant scene skipped: ${String(error).slice(0, 80)}`);
        }
      },
      () => show("/runs", T.runs, 10000, { scroll: 240 }),
      () => show("/cases", T.cases, 9000, { scroll: 240 }),
      () => show(`/board?po=${rfq}`, T.waiting_mail, 8000),
    ],
    T.waiting_mail,
  );
  rows = await kindsOf(step.outcome?.approval_ids ?? []);
  const award = rows.find((r) => r.kind === "award");
  if (award) await showApproval(award.id, T.award, { ms: 11000, scroll: 380 });
  const awardedPo = award?.po_name ?? rfq;
  if (awardedPo) await show(`/board?po=${awardedPo}`, T.awarded_board, 7000);

  // 6. the short receipt
  step = startStep("short_receipt", false);
  await during(step, [() => show(`/board?po=${receiptPo}`, T.receipt_running, 7000)], T.receipt_running);
  ids = step.outcome?.approval_ids ?? [];
  if (ids.length) {
    await showApproval(ids[0], T.receipt, { ms: 9000, scroll: 300 });
    await approveQuietly(ids.slice(1));
  }

  // 7. the invoice
  step = startStep("invoice_variance", false);
  await during(step, [async () => { await caption(T.bill_running); await sleep(6000); }], T.bill_running);
  ids = step.outcome?.approval_ids ?? [];
  if (ids.length) {
    await showApproval(ids[0], T.bill, { ms: 10000, scroll: 340 });
    await approveQuietly(ids.slice(1));
  }

  // 8. the briefing, and the numbers
  step = startStep("briefing", false);
  await during(step, [async () => { await caption(T.briefing_running); await sleep(5000); }], T.briefing_running);
  await show("/briefing", T.briefing, 12000, { scroll: 460 });
  await show("/ai", T.ai, 10000, { scroll: 300 });
  await show("/", T.home, 5000);
  await card(T.outro_title, T.outro_text, 7000);

  const video = page.video();
  await context.close();
  await browser.close();
  const target = resolve(OUT_DIR, `demo-${LANG}.webm`);
  renameSync(await video.path(), target);
  const seconds = Math.round((Date.now() - started) / 1000);
  log(`video: ${target} (${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, "0")}s)`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
