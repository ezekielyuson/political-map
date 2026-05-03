# Entity resolution (Individuals)

Cluster ``Individual`` nodes into one canonical node per real person, then
merge the originals into the canonical via the existing aliases machinery
(Phase 2's ``merge_nodes``).

## Why not `dedupe`?

The spec calls for the [`dedupe`](https://github.com/dedupeio/dedupe)
library. We tried it; it has C extensions and didn't install on this
Python (3.14) without MSVC build tools. We swapped in
[`rapidfuzz`](https://github.com/maxbachmann/RapidFuzz) and a transparent
blocking + threshold pipeline:

* Wheels everywhere, no build step.
* Same end-state (cluster -> canonical + aliases).
* Easier to audit: every decision is a weighted similarity score with the
  field breakdown stored on the review queue row.
* Trivial to swap in `dedupe` later -- just replace
  ``candidate_pairs()`` with whatever ``dedupe.Dedupe`` produces.

## Pipeline

```
  ┌──────────┐    ┌────────┐    ┌────────┐    ┌──────────┐
  │ extract  │ -> │  block │ -> │ score  │ -> │ threshold│
  └──────────┘    └────────┘    └────────┘    └────┬─────┘
                                                   │
                            ┌──────────────────────┼─────────────────────┐
                       score >= auto              auto > score >= review   score < review
                            │                      │                       │
                            v                      v                       v
                       merge_pair()           review_queue                drop
                       (rapidfuzz)             (pending)
                            │                      │
                            │              human via Streamlit
                            │                      │
                            │              record_decision()
                            │                      │
                            └──────► apply_decisions() ◄─────────
                                       (merge_pair on accepted)
```

## Thresholds

Defaults:

| Band              | Range          | Action                        |
|-------------------|----------------|-------------------------------|
| auto-merge        | ``score >= 0.95`` | ``merge_pair`` immediately   |
| review            | ``0.80 <= score < 0.95`` | row in ``review_queue`` |
| drop              | ``score < 0.80``  | not queued, not merged       |

Both thresholds are CLI-overridable. Lower the auto-merge threshold to
chew through obvious matches faster, then raise the review threshold to
narrow the human queue.

## Field weights

Defined in :data:`pge.resolution.individuals.FIELD_WEIGHTS`. Sum to 1.0.

| Field      | Weight |
|------------|--------|
| name       | 0.55   |
| employer   | 0.25   |
| occupation | 0.10   |
| zip        | 0.10   |

Empty fields contribute 0.0 to that field's term -- so two records that
match perfectly on name but have no employer info top out around 0.55,
which keeps them in the review band rather than auto-merging on name
alone.

## Blocking

We compare records only within the same ``(last_name, first_initial)``
block. So ``DOE, JANE`` and ``Jane Doe`` block together, but ``Doe`` and
``Smith`` never get scored. Punctuation/case are normalized before
keying.

This is naive but cheap. Two corner cases worth knowing:
* **Hyphenated last names** (``"Doe-Smith"``) get normalized to a single
  alphanumeric token, so ``"Doe-Smith, Jane"`` blocks under ``doesmith|j``.
  Won't merge with ``"Doe, Jane"``. We accept this -- the alternative is
  cross-block search.
* **Single-name people / dropped first names** (``"Cher"``,
  ``"DOE, J"``) end up in different blocks from their full-name selves.
  Add manual review entries when you spot one.

## What gets merged

When two records merge:

1. ``merge_nodes`` rewires every incident edge to the canonical id.
2. ``external_ids`` rows move over.
3. An alias row is recorded so old ids still resolve.
4. The non-canonical node row is deleted.

We pick the **lex-min id** as the canonical. Stable, deterministic, easy
to test. A future enhancement would be "node with the most edges wins,"
but at v1 volumes lex-min is fine.

## CLI

```bash
# Run the full pipeline. Picks up any previously-accepted review_queue
# rows on every invocation, then re-scores the rest of the graph.
pge resolve individuals

# Tighter thresholds for higher-precision auto-merging:
pge resolve individuals --auto-merge 0.97 --review 0.85

# Skip applying review-queue decisions on this run (debugging):
pge resolve individuals --skip-review-apply
```

## Streamlit review UI

```bash
pge ui review                    # opens http://localhost:8501
pge ui review --port 9000        # alternate port
```

The UI:
* Shows pairs side-by-side with the per-field similarity bars.
* "Merge" sets ``status='accepted'`` (the next ``pge resolve individuals``
  call performs the merge). "Reject" records do-not-merge.
* Decisions persist to the ``review_queue`` table; closing the browser
  doesn't lose state.

## What's deferred

* **Active learning.** ``dedupe``'s big value-add is iterative training
  with user-labeled examples. The static-weights approach here is good
  enough for v1; revisit when the queue gets noisy.
* **Cross-source individuals.** Today we only see donors (FEC). Once
  SEC EDGAR / FARA / financial-disclosures land, the same pipeline
  applies but the field set should grow (e.g., add an ``addresses`` field).
* **Politicians and PACs.** Phase 2 already canonicalizes politicians
  via the legislators bridge, and PACs are FEC-keyed (no dupes within
  source). When we add multi-source PACs / committees, this module's
  primitives generalize directly -- pull out a generic ``cluster()``
  function then.
