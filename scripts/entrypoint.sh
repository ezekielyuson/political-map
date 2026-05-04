#!/bin/sh
# Container entrypoint for the deploy image.
#
# Layout:
# - The build step bakes a populated DB at /app/data/pge.db (Politicians
#   from the public congress-legislators YAML).
# - At runtime, /data is a Fly volume that survives across boots.
#
# On first boot the volume is empty; we copy the baked DB onto it.
# On subsequent boots (or if a richer DB has been uploaded via SFTP) we
# leave the volume alone -- so any additional ingest a user has done
# from inside the machine persists.
#
# To force a re-seed: delete /data/pge.db on the volume and restart.

set -e

mkdir -p /data

if [ ! -s "$PGE_DB_PATH" ] && [ -s /app/data/pge.db ]; then
    echo "[entrypoint] seeding $PGE_DB_PATH from baked DB"
    cp /app/data/pge.db "$PGE_DB_PATH"
fi

# Idempotent. Applies any schema changes on top of an older volume DB.
uv run pge db init --path "$PGE_DB_PATH"

exec uv run uvicorn pge.api:app --host 0.0.0.0 --port 8080
