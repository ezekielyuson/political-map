# LDA source

Pulls Senate Lobbying Disclosure Act filings from the official
[`lda.senate.gov` API](https://lda.senate.gov/api/redoc/) into the graph.

## Auth

API key is **optional**. With a key:

```
LDA_API_KEY=...
```

Get one at <https://lda.senate.gov/api/auth/register/>. Without a key, the
endpoint still works but rate limits are tighter (~75 req/min vs ~120/min).
The CLI loads `.env` automatically.

## Endpoints used

| Source           | Endpoint              | Maps to                |
|------------------|-----------------------|------------------------|
| Filing list      | `/api/v1/filings/`    | (gates downstream)     |
| Filing.registrant| (nested in payload)   | `LobbyingFirm` node    |
| Filing.client    | (nested in payload)   | `Company` node         |
| Filing (whole)   | (nested in payload)   | `LobbyingContract` edge |

We use a single endpoint because LDA returns the registrant and client
**inline** on each filing -- no extra GETs required.

## Why API instead of bulk XML?

The original spec mentioned quarterly XML downloads, which are still
available at
<https://www.senate.gov/legislative/Public_Disclosure/database_download.htm>.
We use the JSON API instead because:

* Same data, simpler shape (no XML schema parsing).
* First-class incremental filter (`dt_posted_after`).
* Pagination matches FEC / Congress.gov so the ingestor stays uniform.

The bulk XML route is the right tool for a **full historical backfill**
(every filing since 1999, ~5 GB). For incremental + recent-quarter ingest,
the API wins.

## Rate limits

* **~75 req/min anonymous, ~120 req/min keyed.**
* Tenacity backs off 2s -> 60s on 429/5xx.
* Default page size is 25 (capped server-side at 25), so a full quarter
  (~15k filings) is ~600 pages = ~5 minutes keyed, ~8 anonymous.

## Pagination

LDA uses `?page_size=25&page=N` style. The response includes a `next` URL,
which we follow until null. We don't construct page numbers ourselves --
just hand `next` back to the HTTP client.

## Incremental loads

The `ingest_state` table holds:

* `lda.filings.dt_posted_after` -- max `dt_posted` we've successfully
  ingested. After each run we bump the cursor to the most recent
  `dt_posted` we observed.

If `--since` is passed it overrides the cursor for that run.

## ID conventions

| Thing            | Internal id                  |
|------------------|------------------------------|
| Lobbying firm    | `lf:lda:<registrant_id>`     |
| Client (company) | `co:lda:<client_id>`         |
| Contract edge    | `lda:filing:<filing_uuid>`   |

`filing_uuid` is stable across LDA's own systems, so re-running ingest is
naturally idempotent.

## Money handling

`income` and `expenses` arrive as decimal strings (`"120000.00"`). We use
`decimal.Decimal` to avoid float drift, multiply by 100, and store integer
cents. We prefer `income` when both are present, and fall back to
`expenses` when only that side filed (some firms report on the expenses
method).

## Issue codes

Each filing's `lobbying_activities[]` has its own `general_issue_code`
(e.g. `TAX`, `ENV`, `BUD`). We dedupe these onto the contract edge's
`issue_codes` list, preserving first-seen order.

The LDA codebook (full names: "Taxation/Internal Revenue Code", etc.) is
documented at
<https://lda.senate.gov/system/public/uploads/Lobbying_Disclosure_Act_Guidance_Issue_Codes.pdf>.
We store the codes themselves rather than the display names because they
are stable (display strings change occasionally).

## Gotchas

* **Registrations (LD-1) have no income or activities.** We still write the
  contract edge to capture the relationship; ``amount_cents`` is None and
  ``issue_codes`` is empty. Useful for "when did firm X start representing
  client Y?"
* **A registrant can list the same client across many quarters.** Each
  quarterly filing becomes its own edge (different `filing_uuid`,
  different `as_of_date`). Aggregation across edges is a query-time concern.
* **`dt_posted` is not the report period.** It's when the filing landed in
  the LDA system. The ``quarter`` field captures the reporting period.
* **`government_entities` are not yet resolved to GovernmentBody nodes.**
  The activity payload lists targets like "Department of Defense" or
  "U.S. House of Representatives", but matching those names against
  Phase 2's `gov:<systemCode>` nodes requires a name-resolution layer
  we'll build later. For now the targets are silently dropped.
* **A single LDA registrant can cover multiple lobbying firms** (e.g.,
  acquisitions, name changes). Phase 5 dedupe will reconcile those.
* **LD-203 filings** (semi-annual personal contributions from registered
  lobbyists) are also returned by `/filings/`. We don't have an edge type
  that fits them cleanly yet -- we still write a `LobbyingContract` edge
  (with no income/issue codes), which is technically a misnomer. Document
  this as a v2 cleanup.

## CLI examples

```bash
# Pull all 2024 Q2 filings
pge ingest lda --year 2024 --period q2

# Pull anything posted after a given date (incremental)
pge ingest lda --since 2024-07-01

# Sanity run: one page, no archive
pge ingest lda --year 2024 --period q2 --max-pages 1 --no-archive
```
