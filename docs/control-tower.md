# Control Tower

Bulk approvals with filters and keyboard shortcuts, a command palette, and an installable
PWA with web push for new approvals (title and summary only) are part of the app.

The people's side of the system: a React app served by the director under
`/` (the API lives under `/api`, OpenAPI at `/docs` in dev). Screens:

- **Home** (the landing page): service level, late orders, approvals waiting,
  spend and AI cost with their trend, the morning briefing's paragraph, what
  needs a person, and the feed of what ran alone.

- **Board** (`/board`): swimlanes by priority or supplier; every purchase order as a card in the column
  its life is at: proposals, quotation requested, quotation received, order
  confirmed, to receive, received, invoicing (the invoice is being checked
  or the bill is drafted), closed. The card's edge tells the delivery
  state (green on time, amber due soon, red late with the days); its frame
  tells what a person owes it (amber: an approval, purple: an escalation,
  dashed: on hold); the body shows supplier, amount, planned date, the last
  email and the agents' next step. Approvers drag cards where Odoo allows
  (send a proposal, confirm an RFQ, close or cancel an order, with a note
  that lands in the chatter); the other columns follow emails and receipts.
  Badges say when an invoice waits for a check or a receipt had a
  discrepancy. A card opens a side panel with the facts, the pending approval
  resolvable in place, the case history and the "Talk to your AI" chat. Filters:
  search, supplier, buyer, "only with problems". "Check the mailbox" reads the
  inbox right away instead of waiting for the next scheduled poll and says
  what it found.
- **Approvals**: the inbox. Emails are previewed sanitised (no scripts, no
  remote images) and can be edited before sending; order changes show a
  before/after table with per-line toggles; planning runs link to their
  review; escalations show the model's summary and the last events;
  invoices show the verdict and the line-by-line check; the weekly supplier
  scorecards show every supplier's numbers and paragraph. Every
  card says why the agent proposed it and links to Odoo, the Outlook draft
  and the Langfuse trace. Approving here resolves the `sc.approval` in Odoo
  through the bot, so Odoo fires the same agent callback as its own buttons.
- **History** (the last 7 days by default): one PO-centred timeline per case, from
  the event received through rules, tasks, results and approvals to the agent runs.
  Every case has a **"Talk to your AI" chat** (the director agent): questions are answered from
  the case, the order and the policy; instructions ("ask them for a firm
  date", "wait until the 20th", "close this, I cancelled the order") come
  back as a proposed action that an approver confirms before it runs. An
  email asked for in the chat is always shown for approval before it goes
  out, whatever the automatic-send rules say, and replaces any earlier
  draft still waiting for that order.
- **Act now** lives on the board card: a late order or a silent RFQ can run the
  policy's next step at once instead of waiting for its day. (The former
  Exceptions page is still served at `/exceptions` but no longer in the menu:
  the "only with problems" filter, the inbox and the runs screen cover it.)
- **Planning**: the run's lines grouped by supplier, editable quantities and
  min/max, a per-line drawer with the explanation, the 90-day demand and a
  what-if simulation; approving the selected lines is one resume call.
- **Suppliers**: the latest scorecard per supplier from the weekly run: score,
  on-time in-full, observed lead time, reply time, receipt problems, flagged
  changes and the paragraph.
- **Supplier 360**: one supplier on one page: scorecard, orders, prices with the
  supplier's rank per product, quote rounds, profile and the email timeline
  (metadata only).
- **Risk**: stockout odds at 30 and 60 days per product, expected late lines per
  supplier and the cash exposed; one click starts a quote round or an alternative
  source.
- **Autonomy**: the rules, their 30-day preview, the second approval when a rule
  widens, and the feed of automatic actions with revert.
- **Playbooks**: each plan's steps and the orders currently on each step.
- **Briefing** and **Assistant**: the morning briefing per day, and the
  department-wide chat with citations.
- **AI performance**: automation rate by kind, turnaround, edit and rejection
  rates, ETA error, forecast WAPE, invoices matched first time, cost per agent.
- **Runs**: agent runs with model, tokens, cost and duration; scheduler runs.
- **Demo**: the scripted eight-step day with the presenter's lines (see
  [demo.md](demo.md)).
- **Settings** (admins): model per agent, follow-up policy, which suppliers
  and which email kinds go out without approval (for example reminders and
  delivery date requests automatic, RFQs and purchase orders approved), and
  planning defaults; versioned in `settings_history` and picked up by every
  service within a minute (no restart).

Setup and daily use:

```bash
just ui-create-user ana@example.com "Ana" approver   # roles: viewer | approver | admin
just ui-install          # npm install
just ui-dev              # Vite on http://localhost:5173 with /api proxied to the director
just ui-check            # typecheck, unit tests, production build
just ui-e2e              # Playwright: approve and planning flows, roles, axe, phone viewport
just ui-openapi          # regenerate openapi.json and the typed client after an API change
just image-director      # the director image with the bundle built in
```

The UI has an EN/ES switch (`messages.en.json` / `messages.es.json`); the
default is English. Sessions are JWTs from `POST /api/auth/login`
(`SC__UI__JWT_SECRET`, falling back to the events secret; TTL
`SC__UI__JWT_TTL_MINUTES`). Live updates come over `GET /api/stream`
(Server-Sent Events through Redis pub/sub), so screens refetch on change
instead of polling; the same events feed the bell (new approvals,
escalations, failed runs) and the pending count on the Approvals entry. Links to Odoo and Langfuse use `SC__ODOO__PUBLIC_URL`
and `SC__LANGFUSE__PUBLIC_URL` (what a browser can reach; compose sets
them to `localhost`), not the in-network service URLs; notes and To-Dos in
Odoo link back to the Control Tower through `SC__UI__PUBLIC_URL`. CI regenerates the
client from the director's OpenAPI document and fails on drift.
