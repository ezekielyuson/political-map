# syntax=docker/dockerfile:1.7
#
# PGE deploy image. Single-stage; small enough that multi-stage isn't worth
# the complexity at this size.
#
# Build phases:
# 1. install deps (cached when only source changes)
# 2. install the project itself
# 3. bake a seed DB into the image -- ~535 Politician nodes from the
#    public congress-legislators YAML, no API key required. The container
#    can serve real data the moment it boots.

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

# 3. bake the seed DB. Needs network for the legislators YAML download
# (~2 MB from raw.githubusercontent.com). If the build happens offline,
# this step fails loudly -- the user can retry with network or comment it
# out and seed via SFTP later.
RUN mkdir -p /app/data/ref && \
    uv run pge db init --path /app/data/pge.db && \
    uv run pge ingest congress --entity bootstrap \
        --legislators-cache /app/data/ref \
        --path /app/data/pge.db

COPY scripts/entrypoint.sh /app/scripts/entrypoint.sh
RUN chmod +x /app/scripts/entrypoint.sh

# Runtime: SQLite lives on a Fly volume mounted at /data. PGE_DB_PATH is
# honored by both the API factory (pge.api.app.create_app) and the CLI.
ENV PGE_DB_PATH=/data/pge.db

EXPOSE 8080

CMD ["/app/scripts/entrypoint.sh"]
