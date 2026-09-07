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
    {{UV}} run ruff format src tests scripts

# Lint with ruff, applying safe fixes
lint:
    {{UV}} run ruff check --fix src tests scripts

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

# Build the container image of one member
image member="director":
    docker build -f docker/base.Dockerfile --build-arg MEMBER={{member}} -t scai/{{member}} .

# --------------------------------------------------------------------
# Packaging & cleaning

# Build wheels for every member into dist/
build:
    {{UV}} build --all-packages

# Remove build, test and cache artifacts
clean:
    {{UV}} run python scripts/clean.py
