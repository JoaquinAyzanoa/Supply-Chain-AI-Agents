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
just up          # postgres + redis + odoo + director in docker
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
taken on developer machines); override with `SC_DIRECTOR_PORT`. Odoo publishes
on 8069 (`SC_ODOO_PORT`); see `odoo/README.md`.

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
