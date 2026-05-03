# Congress.gov source

Pulls members and committees from
[api.congress.gov](https://api.congress.gov/) into the graph, and performs
the **first entity-resolution moment**: merging FEC-keyed politician nodes
(from Phase 1) into bioguide-keyed canonical nodes.

## Auth

Same api.data.gov system as FEC -- one key works for both. Set either:

```
CONGRESS_API_KEY=...
# or, if FEC_API_KEY is already set, that's reused as a fallback.
```

Get a key at <https://api.data.gov/signup/>.

## Endpoints used

| Entity              | Endpoint                                  | Maps to               |
|---------------------|-------------------------------------------|-----------------------|
| Members (list)      | `/v3/member`                              | (gates the detail)    |
| Member detail       | `/v3/member/{bioguideId}`                 | `Politician` node     |
| Committees (list)   | `/v3/committee`                           | `GovernmentBody` node |
| Committee detail    | `/v3/committee/{chamber}/{systemCode}`    | `CommitteeMembership` edge per current member |

## Rate limits

* **5,000 req/hr per key** (looser than FEC's 1,000).
* Tenacity backs off exponentially on 429/5xx.
* `members` ingest does N+1 calls (1 list + 1 per bioguide). For ~535 current
  members that's well under budget; running with `--all-members` (historical)
  is closer to ~12k calls and will burn most of the hourly quota.

## Pagination

Limit/offset only. We default to `limit=250` and follow `pagination.next` until
absent.

## Entity resolution

The headline feature for this source.

The bridge between bioguide and FEC ids comes from
[`unitedstates/congress-legislators`](https://github.com/unitedstates/congress-legislators)
-- specifically `legislators-current.yaml` (and optionally
`legislators-historical.yaml`). Each legislator record looks like:

```yaml
- id:
    bioguide: L000174
    fec:
      - S0VT00033
      - S6VT00065
    opensecrets: N00009918
    ...
```

`resolve.py` downloads these YAMLs (caches under `data/ref/`) and builds a
bidirectional bioguide<->FEC index.

### Why YAML and not Congress.gov directly?

The Congress.gov member detail response **does not include FEC ids**. The
unitedstates/congress-legislators repo is the canonical, hand-curated bridge.
It's updated continuously by Congress staff and civic-tech volunteers.

### Resolution flow during `members` ingest

For each Congress.gov member:
1. Compute canonical id `pol:<bioguideId>`.
2. Look up FEC ids for the bioguide via the legislators index.
3. For each FEC id, check the DB for a pre-existing `pol:<fec_id>`
   (created earlier by the FEC ingest).
4. Upsert the canonical bioguide-keyed node.
5. For every existing FEC-keyed node found, call `merge_nodes`:
   - moves its `external_ids` to the canonical row,
   - rewrites every incident edge to use the canonical id,
   - records an alias `pol:<fec_id> -> pol:<bioguideId>`,
   - deletes the source row.

After resolution: one logical politician = one row, with two `external_ids`
entries (`{"congress": "L000174", "fec": "S0VT00033"}`), and any old FEC id
points at the canonical via the `aliases` table.

### Order of operations

`Congress` ingest **after** `FEC` ingest is the happy path -- everything
gets merged in a single pass.

If you ingest FEC *after* Congress, you'll have orphan `pol:<fec_id>` nodes.
Fix it with:

```bash
pge ingest congress --entity resolve
```

This is a no-fetch pass that just runs the merge step against the current DB
using the cached legislators YAML.

## ID conventions

| Thing            | Internal id            |
|------------------|------------------------|
| Politician       | `pol:<bioguideId>`     |
| Government body  | `gov:<systemCode>`     |
| Assignment edge  | `congress:assign:<systemCode>:<bioguideId>` |

## Gotchas

* **Member detail wraps under `member`** (singular), not `members`. Some
  cached API examples use `members`; we tolerate both.
* **`currentMembers` vs `members`** on the committee detail: newer responses
  use the former, older snapshots the latter. We check both via
  `CommitteeDetail.all_members()`.
* **`district` arrives as `int`** for House but is omitted for Senate. We
  type it `int | str | None`.
* **A bioguide can map to multiple FEC ids** (one per candidacy). All of
  them resolve to the same canonical politician.
* **Committee `chamber` is `"NoChamber"`** for some legislative-branch
  agencies (Office of Congressional Workplace Rights, etc.). We skip the
  detail call for those because the detail endpoint requires a chamber slug
  and there isn't one.
* **`partyHistory` is sometimes empty** for newly-seated members. We fall
  back to the latest term's `partyName`.
* **House votes / Senate votes are deferred.** Congress.gov has House votes
  via `/house-vote/...` (added 2024); Senate votes are not in the API and
  require scraping <https://www.senate.gov/legislative/votes_new.htm>.
  Will revisit when bills land in the graph (we'd need `BillNode` to point
  the `BillVote` edges at).

## CLI examples

```bash
# Pull every current member (and merge any matching FEC nodes)
pge ingest congress --entity members

# Pull all committees + per-committee assignments
pge ingest congress --entity committees

# After a late FEC ingest, re-run the merge pass (no fetching)
pge ingest congress --entity resolve

# Sanity run: one page of members, no archive
pge ingest congress --entity members --max-pages 1 --no-archive
```
