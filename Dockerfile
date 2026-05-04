# syntax=docker/dockerfile:1.7
#
# PGE deploy image. Single-stage; small enough that multi-stage isn't worth
# the complexity at this size.
#
# Layout: layer 1 installs the locked deps (cached when only source changes);
# layer 2 copies source and installs the project itself.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 1. deps only -- rebuilds only when pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 2. source + project install.
COPY src ./src
RUN uv sync --frozen --no-dev

# SQLite lives on a Fly volume mounted at /data. PGE_DB_PATH is honored by
# both the API factory (pge.api.app.create_app) and the CLI commands.
ENV PGE_DB_PATH=/data/pge.db

EXPOSE 8080

# Init the DB on every boot -- idempotent, applies any new schema.
# Then exec into uvicorn so Fly's SIGTERM reaches it cleanly.
CMD ["sh", "-c", "mkdir -p /data && uv run pge db init --path \"$PGE_DB_PATH\" && exec uv run uvicorn pge.api:app --host 0.0.0.0 --port 8080"]
