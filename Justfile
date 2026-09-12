# Developer tasks for the Supply Chain AI Agents workspace.
#
# Every recipe line is one plain command so it runs the same in bash (CI,
# Linux, macOS) and in PowerShell (Windows). Anything that needs a loop or
# platform logic lives in scripts/ as a small Python file.

set dotenv-load := true
set shell := ["bash", "-cu"]
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# --------------------------------------------------------------------
# Variables

UV := "uv"
COMPOSE := "docker compose -f infra/docker-compose.yml"
PORT := "8000"

# --------------------------------------------------------------------
# Default / Help

# Show available commands
default:
    @just --list

# --------------------------------------------------------------------
# Setup

# Install every workspace member and the dev tools into .venv
sync:
    {{UV}} sync --all-packages

# Create .env from .env.example when it does not exist
env:
    {{UV}} run python scripts/ensure_env.py

# Install the pre-commit hooks into .git
hooks:
    {{UV}} run pre-commit install

# --------------------------------------------------------------------
# Run

# Run a member the way its container does (python -m <member>)
run member="director":
    {{UV}} run --package {{member}} python -m {{member}}

# Run a member with auto-reload for development
dev member="director":
    {{UV}} run --package {{member}} uvicorn {{member}}.main:app --reload --host 127.0.0.1 --port {{PORT}}

# Apply pending SQL migrations to the application database
migrate:
    {{UV}} run python -m sc_core.infra.migrate

# --------------------------------------------------------------------
# Quality

# Format, lint, type-check and dependency-check (run before every commit)
qa: fmt lint typecheck deps

# Format with ruff
fmt:
    {{UV}} run ruff format src tests scripts odoo/addons

# Lint with ruff, applying safe fixes
lint:
    {{UV}} run ruff check --fix src tests scripts odoo/addons

# Type-check with mypy
typecheck:
    {{UV}} run mypy src

# Dependency check with deptry, once per workspace member
deps:
    {{UV}} run python scripts/deptry_all.py

# Import every member's main module (catches broken wiring cheaply)
smoke:
    {{UV}} run python scripts/smoke_import.py

# --------------------------------------------------------------------
# Tests

# Run unit tests
test:
    {{UV}} run pytest tests/unit -q

# Run integration tests (need Docker; use testcontainers)
test-int:
    {{UV}} run pytest tests/integration -q -m integration

# Run every test
test-all:
    {{UV}} run pytest tests -q

# Run unit tests with coverage reports (terminal + coverage.xml)
coverage:
    {{UV}} run pytest tests/unit -q --cov=src --cov-report=term-missing --cov-report=xml

# --------------------------------------------------------------------
# Docker

# Start the local stack (app-db, redis, director). Extra args go to compose.
up *args:
    {{COMPOSE}} up -d {{args}}

# Stop the local stack and remove containers (volumes are kept)
down:
    {{COMPOSE}} down

# Follow logs of one service
logs service="director":
    {{COMPOSE}} logs -f --no-log-prefix {{service}}

# Show container status and health
ps:
    {{COMPOSE}} ps

# Re-record the LLM cassettes (tests/fixtures/llm) from the real provider
llm-record:
    {{UV}} run python scripts/llm_record.py

# Regenerate the A2A contract snapshots (tests/fixtures/schemas) after a deliberate change
schema-snapshot:
    {{UV}} run python scripts/schema_snapshot.py

# Create or update a Control Tower user (password from SC_UI_PASSWORD or --password)
ui-create-user email name role="approver" *args:
    {{UV}} run python scripts/ui_create_user.py --email {{email}} --name "{{name}}" --role {{role}} {{args}}

# Publish every local prompt (sc_core, supplier_comms, director) to Langfuse Prompt Management
langfuse-prompts:
    {{UV}} run python scripts/langfuse_prompts.py

# Open the Langfuse UI (admin@scai.local / scai-admin-password on first boot)
langfuse-open:
    {{UV}} run python -c "import webbrowser; webbrowser.open('http://localhost:3000')"

# Build the container image of one member
image member="director":
    docker build -f docker/base.Dockerfile --build-arg MEMBER={{member}} -t scai/{{member}} .

# --------------------------------------------------------------------
# Odoo

ODOO_DB := "scai"
ODOO_MODULES := "base,contacts,mail,product,purchase,stock,purchase_stock,sale_management,purchase_requisition,base_automation,sc_agents"

# Create the Odoo database with demo data and install the modules (idempotent)
odoo-init:
    {{COMPOSE}} run --rm odoo odoo -d {{ODOO_DB}} -i {{ODOO_MODULES}} --stop-after-init
    {{COMPOSE}} up -d odoo

# Install one Odoo module into the existing database
odoo-install module="sc_agents":
    {{COMPOSE}} run --rm odoo odoo -d {{ODOO_DB}} -i {{module}} --stop-after-init
    {{COMPOSE}} restart odoo

# Upgrade one Odoo module after changing its code
odoo-upgrade module="sc_agents":
    {{COMPOSE}} run --rm odoo odoo -d {{ODOO_DB}} -u {{module}} --stop-after-init
    {{COMPOSE}} restart odoo

# Generate an API key for the bot user and store it in .env (SC__ODOO__API_KEY)
odoo-apikey login="sc_agent_bot":
    {{UV}} run python scripts/odoo_apikey.py --login {{login}} --db {{ODOO_DB}}

# Point the Odoo addon at the director (system parameters: url + events secret); safe to repeat
odoo-configure director_url="http://director:8000":
    {{UV}} run python scripts/odoo_configure.py --director-url {{director_url}}

# Load the Sun Hydraulics demo dataset (odoo/demo) into the local Odoo; safe to repeat
odoo-seed *args:
    {{UV}} run python scripts/odoo_seed.py {{args}}

# Create the demo supplier (Proveedor Hidraulica) and an open RFQ for it in the local Odoo
odoo-demo-supplier:
    {{UV}} run python scripts/odoo_demo_supplier.py

# Re-record the Odoo response cassettes (tests/fixtures/odoo) from the live container
odoo-record:
    {{UV}} run python scripts/odoo_record.py

# Open an Odoo shell against the database
odoo-shell:
    {{COMPOSE}} exec odoo odoo shell -d {{ODOO_DB}} --no-http

# Stop Odoo and delete its database and filestore (destructive)
odoo-reset:
    {{COMPOSE}} rm -sfv odoo odoo-db
    docker volume rm scai_odoo-db-data scai_odoo-web-data

# --------------------------------------------------------------------
# Mail (Microsoft Graph)

# Device-code login as the bot mailbox; the session is cached in the app database
mail-login: migrate
    {{UV}} run python -m sc_core.mail.cli login

# Show whether a cached mail session exists and for which account
mail-whoami:
    {{UV}} run python -m sc_core.mail.cli whoami

# Remove the cached mail session
mail-logout:
    {{UV}} run python -m sc_core.mail.cli logout

# Read the mailbox identity and the newest inbox entries through Graph
mail-check:
    {{UV}} run python -m sc_core.mail.cli check

# --------------------------------------------------------------------
# Mail sync and scheduler

# Run one inbox sync from the host with the same wiring as the service
sync-once:
    {{UV}} run --package mail_sync python -m mail_sync.cli sync-once

# Ask the running scheduler to fire the mail sync job now (POST /jobs/mail_sync/run-now)
sync-now:
    {{UV}} run python scripts/run_job.py mail_sync

# Fire any scheduler job by id (mail_sync, po_followups, inventory_planning, supplier_performance)
run-job job="mail_sync":
    {{UV}} run python scripts/run_job.py {{job}}

# --------------------------------------------------------------------
# Packaging & cleaning

# Build wheels for every member into dist/
build:
    {{UV}} build --all-packages

# Remove build, test and cache artifacts
clean:
    {{UV}} run python scripts/clean.py
