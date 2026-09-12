# The director image with the Control Tower built in.
#
#   docker build -f docker/director.Dockerfile -t scai/director .
#
# Stage 1 builds the frontend bundle with Node; stages 2 and 3 are the base
# recipe (docker/base.Dockerfile) for the director member; the bundle is
# copied in and served by the director under / (SC__UI__STATIC_DIR).

# ---- frontend --------------------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/control-tower/package.json frontend/control-tower/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/control-tower ./
RUN npm run build

# ---- builder ---------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
COPY src/sc_core ./src/sc_core
COPY src/director ./src/director

RUN uv sync --frozen --no-dev --no-editable --package director

# ---- runtime ---------------------------------------------------------------
FROM python:3.13-slim-bookworm

RUN groupadd --system app && useradd --system --gid app --home /app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=ui --chown=app:app /ui/dist /app/frontend/control-tower/dist

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SC__SERVICE_NAME=director \
    SC__HTTP__HOST=0.0.0.0 \
    SC__HTTP__PORT=8000 \
    SC__UI__STATIC_DIR=/app/frontend/control-tower/dist

USER app
EXPOSE 8000
CMD ["python", "-m", "sc_core.app.run"]
