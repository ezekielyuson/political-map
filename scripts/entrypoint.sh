#!/bin/sh
# Container entrypoint for the deploy image.
#
# Always-overwrite-from-baked model:
# - The build step bakes a populated DB at /app/data/pge.db (Politicians
#   from the public congress-legislators YAML).
# - On every boot we copy that onto $PGE_DB_PATH, replacing whatever was
#   there. Each deploy = fresh data, no stale state to chase.
#
# Tradeoff: if you SSH into the machine and `pge ingest ...` to add edges
# in-place, your work gets wiped on the next deploy. For now that's the
# right default -- the v1 prod story is "seed via redeploy". When you want
# in-machine persistence, switch this to a "copy only if absent" check
# and document the lifecycle clearly.

set -e

mkdir -p "$(dirname "$PGE_DB_PATH")"

if [ -s /app/data/pge.db ]; then
    echo "[entrypoint] seeding $PGE_DB_PATH from baked DB"
    cp /app/data/pge.db "$PGE_DB_PATH"
else
    echo "[entrypoint] no baked DB at /app/data/pge.db -- starting empty"
fi

# Idempotent. Applies any schema bumps on top of the seeded DB.
uv run pge db init --path "$PGE_DB_PATH"

exec uv run uvicorn pge.api:app --host 0.0.0.0 --port 8080
