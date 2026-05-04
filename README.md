# Political Graph Engine (PGE)

A queryable relationship graph over free, public US federal political data.
Every fact is a typed node or edge with explicit provenance, evidence type,
and confidence — no inferred causation, no invented links.

## What it does

- **Ingests** from public sources (FEC, Congress.gov, Senate LDA), each in
  its own subpackage with `fetch` / `parse` / `to_graph` separation.
- **Stores** as typed nodes + edges in SQLite (Postgres-portable schema).
- **Resolves** the same person across sources via an explicit alias table
  (e.g. an FEC candidate id and a Congress.gov bioguide id collapse to one
  politician node).
- **Queries** via a small read-only API: single nodes, bounded-depth
  neighborhoods, paths between two nodes — all alias-aware.
- **Reviews** ambiguous individual matches via a Streamlit UI backed by a
  `review_queue` table.

## Quick start

```bash
# 1. install deps
uv sync

# 2. set up env (FEC + Congress + LDA keys; LDA is optional)
cp .env.example .env
# edit .env

# 3. init the DB
uv run pge db init

# 4. ingest some data
uv run pge ingest fec      --entity committees --since 2024-01-01
uv run pge ingest fec      --entity candidates --cycle 2024
uv run pge ingest congress --entity members
uv run pge ingest lda      --year 2024 --period q2

# 5. resolve cross-source duplicates
uv run pge ingest congress --entity resolve   # bioguide <-> FEC ids
uv run pge resolve individuals                # cluster FEC donors

# 6. query
uv run pge db stats
uv run pge serve            # FastAPI on http://localhost:8000
uv run pge ui review        # Streamlit on http://localhost:8501
```

## Deploy to Fly.io

The repo ships a `Dockerfile` and `fly.toml` ready for [Fly.io](https://fly.io),
**plus a GitHub Actions workflow** that auto-deploys on every push to `main`.

### One-time setup

1. Install flyctl + auth (only needed for the initial app create):
   ```bash
   curl -L https://fly.io/install.sh | sh
   fly auth login
   fly launch --copy-config --no-deploy   # creates the Fly app, accept defaults
   ```
2. Get a deploy token: `fly auth token` (or [Fly dashboard → Tokens](https://fly.io/dashboard))
3. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `FLY_API_TOKEN`
   - Value: the token from step 2.
4. Push any commit (or trigger the workflow manually from the **Actions** tab).
   Every push to `main` that touches the API now redeploys automatically.

The persistent volume mounts at `/data`, so `data/pge.db` survives restarts
and redeploys. Auto-stop is enabled — the machine spins down when idle and
wakes on the first request (3–5s cold start, $0/month on the free tier).

### Seed data: every deploy ships with ~535 Politicians

The Docker build runs `pge ingest congress --entity bootstrap`, which pulls
the public [`unitedstates/congress-legislators`](https://github.com/unitedstates/congress-legislators)
YAML and seeds a `Politician` node per current member of Congress. **No API
key is required for this** — the YAML is public.

Result: a fresh deploy returns real data immediately:

- `GET /health` → non-zero `nodes_total`
- `GET /nodes/pol:L000174` → Patrick Leahy
- `GET /nodes/pol:D000123` → whoever has that bioguide

There are **no edges** in the seed (the YAML doesn't have donations,
committee assignments, or lobbying). Those still require running the
key-gated ingestors.

### Adding edges + deeper data (optional)

The deployed app exposes only the read API. Long-running ingest still
happens off the prod machine. Two paths:

**A. Local ingest + SFTP up.** Ingest on your laptop, push the resulting
DB onto the Fly volume:
```bash
fly ssh sftp shell -a <your-app>
> put data/pge.db /data/pge.db
> exit
```

**B. SSH into the machine and ingest there.** Set API keys as Fly secrets
(`fly secrets set FEC_API_KEY=...`), then:
```bash
fly ssh console -a <your-app>
$ pge ingest fec --entity committees --since 2024-01-01
$ pge ingest congress --entity members
```
Caveat: machines stop when idle, so long ingest runs need
`auto_stop_machines = "off"` in `fly.toml` for the duration.

The `pge ui review` Streamlit UI is **not** deployed (it's an ops surface
that needs a separate process). Run it locally pointed at the same DB.

## Web frontend

A minimal Next.js 15 app lives in [`web/`](web/README.md). It calls the API
above and renders search, profile, and path-finder pages.

Deploy it on **Vercel**:
1. <https://vercel.com/new> → import this repo.
2. **Root Directory** → `web`.
3. Set env var `NEXT_PUBLIC_PGE_API_URL` to your Fly app URL (e.g.
   `https://political-map.fly.dev`).
4. Deploy.

Subsequent `git push` redeploys both Fly (the API, from repo root) and
Vercel (the frontend, from `web/`) — same commit, two targets.

## Architecture

```
sources/<name>/             one subpackage per source — FEC, Congress, LDA
  fetch.py                  HTTP, retry, pagination, raw archival to disk
  parse.py                  Pydantic models for that source's API shape
  to_graph.py               raw -> typed node/edge upserts
  ingest.py                 orchestrator + cursor (incremental loads)
  README.md                 auth, rate limits, ID conventions, gotchas

schema/
  nodes.py                  Politician / PAC / Company / GovernmentBody / ...
  edges.py                  Donation / CommitteeMembership / LobbyingContract / ...
                            Every edge: evidence_type, source_id, confidence, as_of_date

graph/
  db.py                     SQLite layer. Two hot tables (nodes, edges) +
                            external_ids, aliases, ingest_state, review_queue.
  ingest.py                 idempotent upsert primitives.
  aliases.py                merge_nodes — moves edges + external_ids to canonical,
                            records alias for backwards lookup.
  queries.py                get_node / neighbors / edges_between / find_paths.
                            Returns Pydantic views (NodeView / EdgeView / Subgraph).

resolution/
  individuals.py            extract -> block -> score (rapidfuzz) ->
                            threshold (auto-merge | review queue | drop) -> apply.
  README.md                 weights, thresholds, why rapidfuzz over dedupe.

api/
  app.py                    FastAPI: GET /nodes/{id}, /neighbors, /paths,
                            /edges-between, /health.

ui/
  review.py                 Streamlit human-in-the-loop for borderline matches.

cli.py                      Typer entrypoint: db / ingest / resolve / ui / serve.
```

## Data model invariants

- **Every edge carries provenance.** `evidence_type` ∈ {VERIFIED, REPORTED,
  INFERRED}, plus `source_name`, `source_id`, `as_of_date`,
  `strength`, `confidence`. No edge is ever stored without these.
- **Money is integer cents.** No float drift on aggregates.
- **IDs are caller-supplied and deterministic.** Re-ingesting the same source
  data produces the same id, which makes upserts idempotent. Same person
  across sources collapses into one node via the `aliases` table.
- **Bioguide is canonical for politicians.** FEC candidate ids become aliases
  once a Congress.gov ingest runs (the bridge data comes from
  `unitedstates/congress-legislators`).

## Sources

See each source's README for auth, rate limits, ID conventions, and
field-level gotchas:

- [`src/pge/sources/fec/`](src/pge/sources/fec/README.md) —
  Federal Election Commission (api.open.fec.gov). Committees, candidates,
  itemized contributions.
- [`src/pge/sources/congress/`](src/pge/sources/congress/README.md) —
  Congress.gov v3 API. Members, committees, committee assignments.
  Cross-resolves bioguide ↔ FEC ids via congress-legislators.
- [`src/pge/sources/lda/`](src/pge/sources/lda/README.md) —
  Senate Lobbying Disclosure (lda.senate.gov). Quarterly filings →
  registrants, clients, contracts.

## Build phases (the path that got us here)

1. **Phase 0** — Schema, SQLite layer, idempotent upserts, smoke tests.
2. **Phase 1** — FEC source end-to-end.
3. **Phase 2** — Congress.gov + the first **explicit entity-resolution
   moment**: `merge_nodes` + alias table. Bioguide canonicalizes politicians.
4. **Phase 3** — Senate LDA lobbying.
5. **Phase 4** — Query layer (`get_node` / `neighbors` / `edges_between` /
   `find_paths`) + FastAPI.
6. **Phase 5** — Individual entity resolution via `rapidfuzz` blocking +
   threshold pipeline + Streamlit review UI.

## Testing

`uv run pytest -q` — 115 tests, no network, fixture-backed.
`uv run ruff check src tests` — clean.

## Out of scope for v1

- State-level data (federal only)
- Real-time ingestion (batch is fine)
- API authentication (local dev only)
- Public-facing frontend (Streamlit review is an ops UI, not a product)
- LLM-based edge inference

## License

Public political data; project code MIT.
