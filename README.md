# Supply Chain AI Agents

AI agents that run a company's supply chain workflows on top of Odoo:
supplier communication by email, inventory planning, logistics, invoice
matching and supplier performance. An orchestrator (`director`) routes work
to one container per agent; humans approve every consequential action from
Odoo or from the Control Tower UI.

## Layout

```
src/                     uv workspace members, one directory each: src/<member>/<member>/
  sc_core/               shared library: settings, logging, app factory, schemas, migrations
                         (later: odoo, mail, llm, a2a, graph, prompts)
  director/              orchestrator service
tests/unit               fast, no external services
tests/integration        need Docker (testcontainers)
migrations/              plain SQL, applied by sc_core.infra.migrate
docker/base.Dockerfile   one image recipe for every member (build-arg MEMBER)
infra/                   docker compose files
scripts/                 helpers used by the Justfile
```

Agents and services are added as new members under `src/` as their phase
starts. The full plan with stories and tasks lives in `blueprints/` (local).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) 0.8+ (it installs Python 3.13 itself)
- [just](https://just.systems/) 1.43+
- Docker Desktop

## Quick start

```bash
just sync        # install everything into .venv
just env         # create .env from .env.example
just hooks       # pre-commit hooks
just qa          # ruff, mypy, deptry per member
just test        # unit tests
just up          # postgres + redis + odoo + langfuse + director + mail_sync + scheduler + supplier_comms
just odoo-init   # first time only: create the Odoo database
just odoo-apikey # API key for the bot user, written to .env
just odoo-configure  # tell the Odoo addon where the director is and the events secret
just odoo-seed   # demo dataset: Sun Hydraulics catalogue, two years of history (about 15 min)
curl localhost:8010/health/ready
just down
```

Run a service on the host instead of in docker:

```bash
just dev director      # uvicorn with reload on 127.0.0.1:8000
just run director      # exactly what the container runs
```

The director container publishes on host port 8010 by default (8000 is often
taken on developer machines), mail_sync on 8011, the scheduler on 8012, the
supplier_comms agent on 8013 and the inventory_planning agent on 8014;
override with `SC_DIRECTOR_PORT`, `SC_MAIL_SYNC_PORT`, `SC_SCHEDULER_PORT`,
`SC_SUPPLIER_COMMS_PORT`, `SC_INVENTORY_PLANNING_PORT`.
Odoo publishes on 8069 (`SC_ODOO_PORT`); see `odoo/README.md`.

## Configuration

Everything is an environment variable with the `SC__` prefix and `__` for
nesting (`SC__APP_DB__DSN`). See `.env.example` for the full list and
`src/sc_core/sc_core/infra/settings.py` for the schema. Settings are
validated at startup; a bad value fails the process immediately.

## Email: Microsoft Graph setup (development)

The agents read and send email through Microsoft Graph. In development they
use a free personal Outlook.com mailbox with *delegated* permissions: the
mailbox owner signs in once with a device code, and the app can then act on
that mailbox only. Production (phase 10) switches to *application*
permissions on the company's shared mailbox; no code changes.

One-time setup, no company tenant or licence required:

1. **Mailboxes.** Create a free Outlook.com account for the bot (for example
   `scai.compras@outlook.com`) and any second account (Gmail is fine) to play
   the supplier in tests. Keep the passwords in a password manager, never in
   the repo.
2. **An Entra directory.** The Entra and Azure portals refuse a personal
   account that has no tenant. Sign up at <https://azure.microsoft.com/free>
   with **your own** Microsoft account (not the bot's): the sign-up creates
   a directory where you are the administrator. It asks for a phone and a
   card for identity verification; nothing is charged and app registrations
   stay free.
3. **Register the app.** In <https://portal.azure.com> open *Microsoft Entra
   ID → App registrations → New registration*:
   - Name: `scai-mail-dev`
   - Supported account types: **Any Entra ID tenant + personal Microsoft
     accounts** (shown afterwards as "All Microsoft account users")
   - Redirect URI: platform *Public client/native (mobile & desktop)*, value
     `http://localhost`
4. **Allow the device-code login.** *Authentication → Advanced settings →
   Allow public client flows → Yes*, then save.
5. **Permissions.** *API permissions → Add a permission → Microsoft Graph →
   Delegated*: `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`, `User.Read` and
   `offline_access`. Do not use *Application* permissions here, and do not
   grant admin consent: the bot account consents at its first login.
6. **Configure.** Copy the *Application (client) ID* from *Overview* into
   `.env`:

   ```
   SC__MAIL__AUTH_MODE=delegated
   SC__MAIL__CLIENT_ID=<application (client) id>
   SC__MAIL__AUTHORITY=https://login.microsoftonline.com/consumers
   SC__MAIL__MAILBOX=me
   ```

7. **First login.** `just mail-login` prints a code and a URL; open the URL,
   enter the code and sign in as the **bot** account. The refresh token is
   cached in the app database, so this happens once. `just mail-check`
   confirms the mailbox is reachable.

Registering the app does not expose your own inbox: delegated permissions
only cover the account that signs in to the app and accepts the consent.

## LLM providers and Langfuse

Every model call goes through `sc_core.llm`, which wraps Microsoft Agent
Framework's OpenAI chat-completions client. Providers and models are declared
in `src/sc_core/sc_core/llm/models.yaml` (DeepSeek, OpenAI, any local
OpenAI-compatible server); keys come only from the environment.

```
DEEPSEEK_API_KEY=sk-...                  # default model is deepseek-v4-flash
SC__LLM__DEFAULT_MODEL=deepseek-v4-flash
SC__LLM__MODEL__SUPPLIER_COMMS=gpt-5.4   # per-agent override (needs OPENAI_API_KEY)
```

`get_chat_client("supplier_comms")` returns the traced client for that agent;
`complete_structured` validates JSON answers against a Pydantic model; a
`RunBudget` caps tokens and USD per run.

Langfuse (self-hosted, `infra/compose.langfuse.yml`) receives one generation
per model call with the complete input (system prompt, messages, tools,
response format), output, usage and cost, plus spans for tool executions.
`just up` starts it; the first boot creates the project and the API keys that
`.env.example` already contains, and `just langfuse-open` opens the UI
(`admin@scai.local` / `scai-admin-password`). The MinIO container in that
stack is a Langfuse-internal dependency; the project stores no emails or
attachments there.

LLM tests run offline: unit tests use scripted clients or recorded cassettes
(`just llm-record` refreshes `tests/fixtures/llm/` from the real provider).
`just test-int` runs the Langfuse ingest check against the compose stack and,
when `DEEPSEEK_API_KEY` is set, the provider capability checks that back the
flags in `models.yaml`.

`just langfuse-prompts` publishes every local prompt file (`sc_core`,
`supplier_comms`, `director`) to Langfuse Prompt Management with the
`production` label, so `get_prompt` serves them from Langfuse instead of
logging a fallback on every call; edit a prompt there and the agents pick it
up within `SC__LANGFUSE__PROMPT_CACHE_SECONDS`.


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
- **Forecast** (`forecasting/`, plain Python): weekly buckets, moving
  average, simple and Holt exponential smoothing and Croston; a
  rolling-origin backtest picks the method with the lowest WAPE, ties go
  to the simpler one, intermittent series go to Croston, short series get
  a moving average without an error estimate.
- **Policy** (`policy/formulas.py`): `SS = z·sqrt(LT·σd² + d²·σLT²)`,
  `ROP = d·LT + SS`, order-up-to `d·(LT + R) + SS`, order quantity raised
  to the MOQ; parameters per product in `planning_params` with defaults by
  ABC class (revenue share). Every stored line keeps its inputs, so any
  quantity recomputes by hand.
- **Exceptions and explanations**: rules flag stockout risk, negative
  position, overstock, missing supplier, missing history and lead-time
  drift and decide the action (rule change, RFQ, both, manual review). The
  model writes a Spanish explanation per exception line and a run summary;
  a guard test proves it changes nothing else.
- **Approval and apply**: one `sc.approval` of kind `planning_run` per run,
  hung on the warehouse; the callback may name accepted lines and edit
  quantities (Control Tower, phase 8), a plain Odoo approval accepts every
  actionable line. Apply writes reorder rules, creates one draft RFQ per
  supplier with an idempotent external ref and publishes `rfq.drafted`, which
  the director turns into `supplier_comms.send_rfq`.
- **`what_if`** simulates parameter overrides for one product with no
  approval and no writes. `just run-job inventory_planning` runs the daily
  plan on the demo and leaves the approval pending in Odoo. Model answers for
  the unit tests are replayed from `tests/fixtures/llm/inventory_planning.json`.

## Orchestrator

`director` is the only service that knows the agents exist. Every event
(`POST /events`, signed) is routed by a table, never by a model:
`inbound_mail.linked` → `handle_inbound`, `inbound_mail.unlinked` →
`resolve_unlinked` or straight to a person when the sender is unknown,
`odoo.purchase_confirmed` → `send_po`, `odoo.receipt_validated` and
`odoo.orderpoint_triggered` recorded for phases 9 and 7, `odoo.approval_resolved`
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

## Control Tower

The people's side of the system: a React app served by the director under
`/` (the API lives under `/api`, OpenAPI at `/docs` in dev). Screens:

- **Board** (the landing page): every purchase order as a card in the column
  its life is at: proposals, quotation requested, quotation received, order
  confirmed, to receive, received, closed. The card's edge tells the delivery
  state (green on time, amber due soon, red late with the days); its frame
  tells what a person owes it (amber: an approval, purple: an escalation,
  dashed: on hold); the body shows supplier, amount, planned date, the last
  email and the agents' next step. Approvers drag cards where Odoo allows
  (send a proposal, confirm an RFQ, close or cancel an order, with a note
  that lands in the chatter); the other columns follow emails and receipts.
  A card opens a side panel with the facts, the pending approval resolvable
  in place, the case history and the "Talk to your AI" chat. Filters:
  search, supplier, buyer, "only with problems". "Check the mailbox" reads the
  inbox right away instead of waiting for the next scheduled poll and says
  what it found.
- **Approvals**: the inbox. Emails are previewed sanitised (no scripts, no
  remote images) and can be edited before sending; order changes show a
  before/after table with per-line toggles; planning runs link to their
  review; escalations show the model's summary and the last events. Every
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
- **Runs**: agent runs with model, tokens, cost and duration; scheduler runs.
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

## Demo dataset

`odoo/demo/sun_hydraulics.yaml` describes a small Peruvian distributor of Sun
Hydraulics components: 14 products (counterbalance, relief, needle, flow
control, check, directional, reducing and logic cartridges, a line body, a
coil, a seal kit), two suppliers with prices and lead times (the demo
mailbox as primary, an alternate with no email), four customers, stock,
reorder rules with three deliberate flaws, and the parameters of two years
of history. `just odoo-seed` loads it as the administrator: the catalogue,
then delivered sales orders and received purchases generated from the
per-product demand profiles with a fixed RNG seed, dated back so the planning
and performance agents see real history. It prints a summary (stock, demand
shape, on-time share and observed lead time per supplier, open incoming
orders) and is safe to run again. A fresh demo: `just odoo-reset`,
`just odoo-init`, `just odoo-apikey`, `just odoo-configure`, `just odoo-seed`.
See `odoo/demo/README.md`.

The supplier agent can also send a confirmed order as Odoo's own "Orden de
Compra" PDF (task `send_po`): the report is rendered over RPC, attached to
the cover email, listed in the approval and sent after it.

## Language

Everything internal is English: prompts, reasoning, tool calls, logs, traces
and case events. Only what people read follows a language. Emails to
suppliers are written in the supplier's Odoo language (`res.partner.lang`),
falling back to the instance language; explanations, run summaries,
escalation summaries, approval titles and chatter notes follow
`SC__AGENTS__LANGUAGE` (`en` or `es`, default `en`); the Control Tower has
its own EN/ES switch per user. Those strings
live in one catalog, `sc_core.i18n`, and every prompt that produces text for
a person takes the language as a variable. Odoo renders PDFs and its own UI
in the partner's and the user's language on its own.

## Conventions

- One library is added to a member's `pyproject.toml` by the story that first
  imports it; `just deps` runs deptry per member to keep that honest.
- Every service is built with `sc_core.app.create_application`, so all of them
  share `/health/live`, `/health/ready`, `/discovery`, the request id header,
  the size limit, the security headers and the error body shape.
- Logs never contain email bodies or secrets: the logger redacts by key.
- Conventional commits (`feat:`, `fix:`, `chore:` …), subject under 50 chars.
- Run `just qa` before committing.

## Tests

```bash
just test          # unit
just test-int      # integration (Docker)
just coverage      # unit with coverage.xml
```

## License

MIT, see `LICENSE`.
