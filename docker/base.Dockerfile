# Shared image recipe for every Python member of the workspace.
#
#   docker build -f docker/base.Dockerfile --build-arg MEMBER=director -t scai/director .
#
# Only the selected member and sc_core are installed, as regular (non-editable)
# packages, so the runtime stage needs nothing but the virtualenv.

# ---- builder ---------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ARG MEMBER
RUN test -n "$MEMBER" || (echo "build-arg MEMBER is required (e.g. director)" && exit 1)

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Lockfile and workspace roots first so dependency layers cache well.
COPY pyproject.toml uv.lock ./
COPY src/sc_core ./src/sc_core
COPY src/${MEMBER} ./src/${MEMBER}

RUN uv sync --frozen --no-dev --no-editable --package "${MEMBER}"

# ---- runtime ---------------------------------------------------------------
FROM python:3.13-slim-bookworm
ARG MEMBER

RUN groupadd --system app && useradd --system --gid app --home /app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SC__SERVICE_NAME=${MEMBER} \
    SC__HTTP__HOST=0.0.0.0 \
    SC__HTTP__PORT=8000

USER app
EXPOSE 8000
CMD ["python", "-m", "sc_core.app.run"]
