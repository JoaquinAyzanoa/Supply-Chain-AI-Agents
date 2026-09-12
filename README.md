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
just up          # postgres + redis + odoo + langfuse + director + mail_sync + scheduler
just odoo-init   # first time only: create the Odoo database
curl localhost:8010/health/ready
just down
```

Run a service on the host instead of in docker:

```bash
just dev director      # uvicorn with reload on 127.0.0.1:8000
just run director      # exactly what the container runs
```

The director container publishes on host port 8010 by default (8000 is often
taken on developer machines), mail_sync on 8011 and the scheduler on 8012;
override with `SC_DIRECTOR_PORT`, `SC_MAIL_SYNC_PORT`, `SC_SCHEDULER_PORT`.
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

## Mail sync and scheduler

Two deterministic services run without any model call.

**scheduler** fires the cron table with signed HTTP dispatches: the inbox
sync every 30 minutes and, for the director, the follow-up, planning and
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
