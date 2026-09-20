# Agents and services

How each piece works, in the order work flows through the system. Every number that
matters comes from code; the model reads, writes text and explains. For how to run
the stack see the [README](../README.md) and [setup](setup.md).

## Orchestrator

`director` is the only service that knows the agents exist. Every event
(`POST /events`, signed) is routed by a table, never by a model:
`inbound_mail.linked` → `handle_inbound`, `inbound_mail.unlinked` →
`resolve_unlinked` or straight to a person when the sender is unknown,
`odoo.purchase_confirmed` → `send_po`, `odoo.receipt_validated` →
`reconcile_receipt`, `odoo.orderpoint_triggered` → `review_product`, `odoo.approval_resolved`
and `agent.run_finished` mirrored on the case. The work itself runs as a
Microsoft Agent Framework workflow built per event: route → one A2A proxy
per agent (at most `SC__A2A__MAX_CONCURRENT` runs in flight) → consolidate.

Every piece of work belongs to a **case** (`cases`, `case_events`,
migration 004): one purchase order story, attached by order and Outlook
conversation, with the case id as the Langfuse session. Each agent task
runs on its own thread (the event's id) so a second email on a paused
thread never clobbers the run waiting for approval. Case events keep
identifiers and short summaries only.

Odoo emits its events itself: the `sc_agents` addon posts the same signed
envelope every producer uses, after commit, when an order is confirmed, a
receipt validated or an approval resolved (`just odoo-configure` stores the
director url and the events secret as system parameters). Agents publish
`agent.run_finished` after a resume, so a case leaves `awaiting_approval`
even though the approval callback never passes through the director.

The daily `po_followups` job (`just run-job po_followups`) applies the
follow-up policy: silent RFQs get a `follow_up` after `SC__DIRECTOR__RFQ_NO_REPLY_DAYS`
(3 then 7 days) and are escalated after that, orders past their planned
date get a `request_eta` after one day and are escalated after four, orders
due within five days get one ETA request, pending approvals are reminded
after two days and expired after seven. Each rule fires once per order and
is written to `case_events` as `rule_fired`, so the UI can explain every
email. At most `SC__DIRECTOR__MAX_ACTIONS_PER_RUN` emails and escalations
go out per run (the rest waits for the next one). The same job replays
events that were deferred (order locked) and reconciles orders confirmed
while an event was missed, looking back `SC__DIRECTOR__RECONCILE_SINCE_DAYS`
but never before the orchestrator's first case. Jobs run in the background;
their summary lands on the tick's `event_inbox` row.

Escalations are `sc.approval` records of kind `escalation` on the order,
with a three-sentence summary written by the model (the director's only
model call, replayed in tests from `tests/fixtures/llm/director.json`), the
reason and the Langfuse trace link, plus a To-Do for the approver. A
resolved escalation closes its case; escalations are reminded but never
expired. One run
per order at a time is guaranteed by a Redis lock (`po:<name>`); an event
that cannot get it within `SC__DIRECTOR__LOCK_WAIT_SECONDS` stays in the
inbox for the replay.

## Autonomy, reasoning and playbooks

What may run without a person is a rule that people set, never a model's
choice.

- **Autonomy policy**: rules match an action's facts (kind, supplier, email
  kind, amount cap, confidence floor, change limits) and set a level:
  `approve` (a person decides) or `auto_notice` (runs alone, reported in the
  "done automatically" feed with a revert window where a revert exists).
  Awards, counter-offers and autonomy changes can never be automated.
  Widening a rule needs a second person; every draft shows a 30-day preview
  of what it would have let through.
- **Reasoning on every approval**: the facts the agent knew, the rule or step
  that led here, its confidence, what else the person can do, and the
  counterfactual (what would have let it run alone, or why it never will),
  written in the instance language.
- **Learning from decisions**: edits and rejections are recorded, a weekly
  calibration suggests rule changes, and supplier profiles and the planner's
  memory carry what people corrected.
- **Playbooks** (`director/playbooks/*.yml`): multi-step plans that run for
  days (late order, silent RFQ, receipt discrepancy, quote round, internal
  request, new supplier): ask, wait, chase, escalate, find another source.
  The follow-up job starts them; the hourly tick and order events move them.
- **Briefing and assistant**: a morning briefing built from facts with one
  model-written paragraph (07:30 on weekdays, kept per day, emailed to the
  recipients in Settings), and a department-wide assistant that answers with
  citations and turns instructions into plans an approver confirms once.

## Mail sync and scheduler

Two deterministic services run without any model call.

**scheduler** fires the cron table with signed HTTP dispatches: the inbox
sync every 5 minutes and, for the director, the follow-up, planning and
performance jobs (their handlers arrive with the agents). Crons are
`SC__SCHEDULER__*_CRON` in `SC__TIMEZONE`; `GET :8012/jobs` shows next fire
times and the last run; `just sync-now` (or `just run-job <id>`) fires one
now. Run history lives in `scheduler_runs`.

**mail_sync** pulls inbox changes from Graph with delta queries, skips what
it already handled, links each message to a purchase order by rule and
POSTs one typed event per message to the director. The rules, in order:
our `x-sc-po` header, the `[P00015]` subject token, a conversation already
linked, `In-Reply-To` against something we sent, and a supplier with exactly
one open order (flagged as a heuristic). Anything else is reported as
unlinked with the candidate orders so the supplier agent (phase 5) decides.
The link lands in Odoo as `sc.mail.link` (visible in the PO's "AI Agent" tab
with an "Open in Outlook" link); the app database keeps only Graph ids, the
delta link and Message-IDs. No subject, body or sender name is stored
anywhere. `just sync-once` runs one sync from the host and prints the report.

Every internal call carries `X-SC-Signature`, an HMAC-SHA256 of the body
with `SC__EVENTS__SIGNING_SECRET` (the same scheme the Odoo approval
callback uses). The director rejects a bad signature with 401 and stores
accepted events idempotently in `event_inbox`; a producer that cannot reach
the director parks the event in its `event_outbox` and retries on the next
run. Apply `migrations/002_mail_sync.sql` with `just migrate` before the
first run.

## Supplier communications agent

`supplier_comms` is the first LangGraph agent. It runs in its own container
and is reachable over A2A (agent card at `/.well-known/agent-card.json`,
JSON-RPC at `/a2a` behind the bearer token `SC__A2A__TOKEN`, which falls back
to the events secret). Five tasks:

- `send_rfq`, `request_eta`, `follow_up`: draft an email from the order
  lines with read-only tools (order lines, supplier price history, open
  orders), create the Outlook draft, ask for a `send_email` approval in Odoo
  with the full body, and send after approval. Suppliers listed in
  `SC__SUPPLIER_COMMS__AUTO_SEND_PARTNER_IDS` skip the approval.
- `handle_inbound`: classify the supplier's reply (quotation, ETA update,
  question, other), extract prices, lead times and the delivery date, diff
  against Odoo, ask for a `po_change` approval, then write planned dates and
  price-list entries. Questions are answered in the thread; anything else
  ends as no action.
- `resolve_unlinked`: pick the order a message belongs to among candidates,
  or escalate to a person.

Approvals pause the graph: LangGraph checkpoints the state in the app
database, Odoo shows the request (and a To-Do for `SC__AGENTS__APPROVER_USER_ID`),
and the decision comes back to `POST /approvals/callback` on
`SC__SUPPLIER_COMMS__PUBLIC_URL`, signed with the events secret. Callbacks are
safe to repeat. Every run is a `sc.agent.run` in Odoo with a link to its
Langfuse trace; the supplier's text lives in the state only while the run is
active and is cleared before it ends.

The director hands linked and unlinked mail events to the agent as soon
as it accepts them (`SC__A2A__SUPPLIER_COMMS_URL`). Model answers for the
unit tests are recorded cassettes (`just llm-record`). To try the loop
on the demo supplier: `just odoo-demo-supplier` creates "Proveedor
Hidraulica" with an open RFQ, and `just test-int` runs the live test (a real
RFQ goes out to the supplier mailbox; set `SC_E2E_SUPPLIER=1` and reply from
Gmail to also exercise the inbound half).

## Inventory planning agent

`inventory_planning` runs the daily replenishment plan (scheduler job
`inventory_planning`, 06:00) and single-product reviews (an Odoo reorder
rule that fires routes to `review_product`). Numbers come from code; the
model only explains.

- **Data** (`sc_core.odoo.repositories.planning`): demand per product and
  day from sales order lines in state sale/done, dated by the order (shipped
  moves are a cross-check), stock on hand in the warehouse's internal
  locations, confirmed purchase lines still to receive, supplier terms
  (delay, MOQ, price) and the current reorder rules. `SC__PLANNING__PRODUCT_CATEGORY`
  limits the run to one category subtree (the demo uses `Hidráulica`).
- **Forecast** (`domain/forecasting/`, plain Python): weekly buckets, moving
  average, simple and Holt exponential smoothing and Croston; a
  rolling-origin backtest picks the method with the lowest WAPE, ties go
  to the simpler one, intermittent series go to Croston, short series get
  a moving average without an error estimate.
- **Policy** (`domain/policy/formulas.py`): `SS = z·sqrt(LT·σd² + d²·σLT²)`,
  `ROP = d·LT + SS`, order-up-to `d·(LT + R) + SS`, order quantity raised
  to the MOQ; parameters per product in `planning_params` with defaults by
  ABC class (revenue share). Every stored line keeps its inputs, so any
  quantity recomputes by hand.
- **Exceptions and explanations**: rules flag stockout risk, negative
  position, overstock, missing supplier, missing history and lead-time
  drift and decide the action (rule change, RFQ, both, manual review). The
  model writes an explanation per exception line and a run summary, in the
  instance language;
  a guard test proves it changes nothing else.
- **Approval and apply**: one `sc.approval` of kind `planning_run` per run,
  hung on the warehouse; the callback may name accepted lines and edit
  quantities (Control Tower), a plain Odoo approval accepts every
  actionable line. Apply writes reorder rules, creates one draft RFQ per
  supplier with an idempotent external ref and publishes `rfq.drafted`, which
  the director turns into `supplier_comms.send_rfq`.
- **`what_if`** simulates parameter overrides for one product with no
  approval and no writes. `just run-job inventory_planning` runs the daily
  plan on the demo and leaves the approval pending in Odoo. Model answers for
  the unit tests are replayed from `tests/fixtures/llm/inventory_planning.json`.

## Sourcing agent

`sourcing` asks the market instead of trusting one price list. The planner
proposes a need (product, quantity, date) without choosing a supplier; the
sourcing agent does the rest. Four tasks: `quote_round`, `compare_quotes`,
`counter_offer`, `alternate_source`.

- **Quote rounds**: one RFQ per supplier who lists the product, grouped as
  alternatives in Odoo, each with its own case on the board. The emails go
  out through the supplier agent, so they follow the same approvals and
  autonomy rules as every other email. A round closes when every supplier
  answered or after `sourcing_deadline_days` (runtime setting, default 5).
- **Comparison** (`domain/compare.py`, no model): landed unit cost (quote plus the
  freight estimate, `sourcing_freight_pct`), lead time from the quote, and the
  supplier's measured score. Each is normalised and weighed
  (`SC__SOURCING__WEIGHT_PRICE` 0.6, `WEIGHT_LEAD_TIME` 0.2, `WEIGHT_SCORE`
  0.2; a partial basket pays `INCOMPLETE_PENALTY`). Every quote gets one
  composite number and a list of reasons in words; the default award per line
  follows the recommendation and names the cheapest as the alternative. The
  model only writes the recommendation paragraph from those numbers.
- **Award**: always a person's decision (`award` is in the never-automated
  kinds). All lines to one supplier or line by line; the winner's RFQ is
  confirmed in Odoo, the others get a courteous decline and are cancelled.
- **Counter-offers** (`domain/negotiation.py`, no model): the target is the lowest of
  the buyer's target, the last paid price and the best competing quote; the
  offer never goes below the floor (`negotiation_cap_pct`, default 10%) and
  stops after `negotiation_max_rounds` (default 2). Always approved by a
  person, who may edit the price within the limits.
- **Alternative sources**: for a late order, the agent compares who else
  lists the products (price list and score) and proposes a round.
- **New suppliers**: a quote from an unknown sender becomes a `partner_create`
  approval before it enters the round.

## Logistics agent

`logistics` closes the loop between the order and the warehouse. It reuses
the supplier agent's ports as a library (one place knows how to read an
email from Graph and send a draft) and has its own graph, task and result.

- **Shipping notices**: when the supplier agent classifies an email as
  `shipping_notice`, the director sends `track_shipment` on the same case.
  The model extracts carrier, tracking number, dispatch and arrival dates
  (`prompts/extract_shipment.md`); a carrier adapter (`infra/carriers/`, a fake
  for now) may refine the arrival; the proposal is a `po_change` approval
  with source `tracking`. Approved: the accepted lines, every open receipt
  and the order's ETA fields get the date, and the chatter shows the facts.
- **Receipts**: a validated receipt (`odoo.receipt_validated`) becomes
  `reconcile_receipt`. The comparison is arithmetic (`domain/reconcile.py`): counted
  against expected per line, `short` or `over` beyond
  `SC__LOGISTICS__RECEIPT_TOLERANCE_PCT` (default 0, exact), extra move lines
  are `over`. A match leaves a note; a discrepancy becomes an email the model
  writes from the table (`prompts/draft_discrepancy.md`), always approved by
  a person, sent from Outlook like every other email. `report_discrepancy`
  carries a clerk's words (damage, for instance) into that email even when
  the count matches.
- Late pickings need no extra job: the follow-up job already acts on late
  orders by their planned dates.
- Bills and receipts are read through `AccountMoveRepo` and
  `StockMoveLineRepo`; the accounting module is installed with
  `just odoo-install account` on an existing database.

## Invoice matching agent

`invoice_match` checks a supplier's invoice against the order and what was
received, and never posts an accounting entry.

- **Inputs**: an email the supplier agent classifies as `invoice` (the PDF
  text and the body go to the model, `prompts/extract_invoice.md`, which
  copies the numbers as printed; a structured XML e-invoice would skip the
  model through the `parse_xml` hook), or a vendor bill a person typed in
  Odoo (`odoo.bill_created`, already structured, no model call).
- **Matching** (`domain/matching.py`, no model): the order comes from the task, the
  invoice's reference, the email text, or the supplier's open orders by
  total (one match, or the case is escalated). Lines map by the product
  code in Odoo's product name, then by description similarity; each line is
  `ok`, `price_variance` (beyond `SC__INVOICE_MATCH__PRICE_TOLERANCE_PCT`,
  default 1%), `not_received` (billed more than received and not yet
  billed), `qty_variance` or `unmatched`. An invoice number already recorded
  is left alone.
- **Decision**: one `vendor_bill` approval carries the verdict and the
  table. Clean: approving creates the draft bill from the order (Odoo's own
  action, idempotent); `SC__INVOICE_MATCH__BILL_AUTO_APPROVE_AMOUNT` lets
  clean invoices up to that total through without a person (default 0:
  always ask). Held: approving means "record it anyway", rejecting leaves
  the invoice with the supplier; asking for a credit note is a chat
  instruction on the case. A bill typed in Odoo only gets its match note.

## Supplier performance agent

`supplier_performance` runs every Monday at 07:00 (scheduler job
`supplier_performance`, through the director) over the last
`SC__SUPPLIER_PERFORMANCE__MONTHS` (12) of history per active supplier.

- **Metrics in code** (`domain/metrics.py`): OTIF against the first promise (the
  confirmation snapshot on the case, else the line's planned date), observed
  lead time (confirmed to received, mean and std, 5% trimmed), promise drift,
  median reply time from the order's emails, receipt problems (the logistics
  agent's discrepancy reports over received lines) and price stability from
  the price list. The score (0 to 100) weighs only the components with data;
  weights are settings (`SC__SUPPLIER_PERFORMANCE__WEIGHT_*`).
- **The model** writes one paragraph per supplier from those numbers
  (`prompts/scorecard.md`) and may add short trend flags; the code flags lead
  time up 30%, OTIF down ten points, slower replies and more receipt
  problems against the previous run.
- **One approval per run** (`supplier_score`): approving writes the partner
  fields (`sc_score`, `sc_otif`, `sc_lead_time_mean`, `sc_lead_time_std`,
  shown on the partner's "Supplier performance" tab), the price list lead
  times (`product.supplierinfo.delay`) and the planner's parameters
  (`planning_params.lead_time_mean_days`, source `measured`), so the next
  plan uses observed lead times. Observations and scores live in
  `lead_time_observations` and `supplier_scores` (migration 008).
- **Ranking**: `GET /performance/rank/{product_id}` on the agent (bearer)
  joins the latest scores with the price list, ordered by score, then price,
  then lead time, each with a one-line reason; the director proxies it and
  the scores to the Control Tower under `/api/performance/*`.
