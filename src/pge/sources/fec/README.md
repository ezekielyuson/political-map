# FEC source

Pulls Federal Election Commission data from
[api.open.fec.gov](https://api.open.fec.gov/developers/) into the graph.

## Auth

1. Register for a free API key at <https://api.data.gov/signup/>
2. Put it in `.env` at the repo root:
   ```
   FEC_API_KEY=your-key-here
   ```
3. The CLI loads `.env` automatically (via `python-dotenv`).

## Endpoints used

| Entity         | Endpoint                  | Maps to                  |
|----------------|---------------------------|--------------------------|
| Committees     | `/committees/`            | `PAC` node               |
| Candidates     | `/candidates/`            | `Politician` node        |
| Contributions  | `/schedules/schedule_a/`  | `Donation` edge          |

Every edge written from this source carries `evidence_type=VERIFIED`,
`source_name="fec"`, and `source_id` set to the FEC `sub_id` (the per-row
transaction id).

## Rate limits

* **1,000 requests/hour per API key.** At `per_page=100`, that's ~100k rows/hr.
* On 429, `tenacity` backs off exponentially (2s -> 60s, 5 attempts).
* Schedule A holds tens of millions of rows -- always scope it (`--committee-id`
  or `--since`) or you'll burn the budget without finishing.

## Pagination

FEC supports two modes; we use both:

* **Offset** (`page` + `per_page`, max 10k results): used for committees and
  candidates.
* **Cursor** (`last_index` + `last_<sort_field>`): required past the 10k cap.
  Schedule A always falls into cursor mode for non-trivial queries.

`paginate()` in `fetch.py` switches modes automatically when the offset cap
runs out.

## Incremental loads

Each entity has a `last_indexed`-style cursor key in the `ingest_state` table:

* `fec.committees.min_last_f1_date`   -- max `last_file_date` we've seen.
* `fec.candidates.min_last_f2_date`   -- ditto for candidates.
* `fec.contributions.min_date`        -- max `contribution_receipt_date`.

If `--since` is passed, it overrides the saved cursor for that run. After a
successful run, the cursor is bumped to the max date observed.

## ID conventions

| Thing                | Internal id                       |
|----------------------|-----------------------------------|
| Committee (PAC)      | `pac:<committee_id>`              |
| Candidate            | `pol:<candidate_id>`              |
| Individual donor     | `ind:fec:<sha1(name|emp|zip|st)>` |
| Donation edge        | `fec:contrib:<sub_id>`            |

Individual donors are hashed because FEC doesn't assign them stable ids.
Same `(normalized_name, employer, zip5, state)` -> same hash, so a donor's
repeated contributions with the same employment context naturally cluster.
**Real entity resolution across these proto-clusters happens in Phase 5
(`dedupe`).** Do not assume a single FEC individual node is a single person.

## Raw archival

By default, every page response is written to
`raw/fec/<endpoint>/page-<n>-<hash>.json` so we can re-parse without
re-fetching. Pass `--no-archive` to skip.

The hash in the filename is a sha1 of the response body, so re-fetching the
same page overwrites itself rather than piling up duplicates.

## Gotchas

* **`organization_type_full` is sometimes null** even when `organization_type`
  is set. We tolerate this in `committee_to_node`.
* **`contribution_receipt_amount` can be negative** for refunds. We clamp to 0
  in `contribution_to_edge` rather than letting it through (negative cents on
  a `Donation` makes downstream aggregation miserable). When we add an
  `EdgeKind="Refund"` we'll start preserving these.
* **`is_individual` and `entity_type` can disagree.** Trust `entity_type`
  when distinguishing committee donors from individuals -- a missing
  `contributor_id` plus `entity_type="IND"` is the reliable individual
  signal.
* **Schedule A hides intermediary committees.** A donation logged here is
  to the committee, not to the candidate. Use the committee->candidate
  relationship (Phase 2 -- via `principal_committees`) to traverse.
* **`sub_id` arrives as either int or string** depending on endpoint version.
  We coerce to str everywhere for stability.

## CLI examples

```bash
# Pull all committees active since 2024-01-01
pge ingest fec --entity committees --since 2024-01-01

# Pull all candidates for the 2024 cycle
pge ingest fec --entity candidates --cycle 2024

# Pull contributions to a single committee (cheap, focused)
pge ingest fec --entity contributions --committee-id C00999999 --since 2024-01-01

# Sanity run: just one page, no disk archive
pge ingest fec --entity committees --max-pages 1 --no-archive
```
