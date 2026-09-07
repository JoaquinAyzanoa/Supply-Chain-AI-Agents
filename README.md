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
just up          # postgres + redis + director in docker
curl localhost:8000/health/ready
just down
```

Run a service on the host instead of in docker:

```bash
just dev director      # uvicorn with reload on 127.0.0.1:8000
just run director      # exactly what the container runs
```

On Windows, if port 8000 is taken (Docker Desktop sometimes holds it), set
`SC_DIRECTOR_PORT=8010` before `just up`.

## Configuration

Everything is an environment variable with the `SC__` prefix and `__` for
nesting (`SC__APP_DB__DSN`). See `.env.example` for the full list and
`src/sc_core/sc_core/infra/settings.py` for the schema. Settings are
validated at startup; a bad value fails the process immediately.

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
