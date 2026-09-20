# Setup and configuration

What the [README](../README.md)'s quick start assumes: settings, the mailbox, the model
provider, the dataset and the language.

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

## Demo dataset

`odoo/demo/sun_hydraulics.yaml` describes a small Peruvian distributor of Sun
Hydraulics components: 30 products (cartridge valves, line bodies and
manifolds, coils, seal kits, hoses and fittings), three suppliers with prices
and lead times (the demo mailbox and two aliases of it, so all three can
quote by email), four customers, stock,
reorder rules with three deliberate flaws, and the parameters of two years
of history. `just odoo-seed` loads it as the administrator: the catalogue,
then delivered sales orders and received purchases generated from the
per-product demand profiles with a fixed RNG seed, dated back so the planning
and performance agents see real history. It prints a summary (stock, demand
shape, on-time share and observed lead time per supplier, open incoming
orders) and is safe to run again. `just odoo-fresh` rebuilds the whole local
stack from zero with it (destructive, asks first); `sun_hydraulics.es.yaml` is
the same company in Spanish (`odoo_fresh.py --dataset`).
An existing database gets a new module with `just odoo-install <module>`
(`just odoo-install account`).
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
