"""Cluster ``Individual`` nodes into canonical entities.

Pipeline
--------
1. **Extract**  — pull ``IndividualNode`` rows + their incident-edge metadata
   into a flat record per node.
2. **Block**    — group nodes by a coarse key (last name + first initial,
   normalized) so we only score within the same block. ``O(B * k^2)`` where
   ``k`` is the average block size — way cheaper than ``O(n^2)``.
3. **Score**    — compute a weighted similarity for each within-block pair
   using :mod:`rapidfuzz`. Weights live in :data:`FIELD_WEIGHTS`.
4. **Threshold** — auto-merge above ``auto_merge_threshold`` (default 0.95).
   Pairs in the ``[review_threshold, auto_merge_threshold)`` band land in
   the ``review_queue`` table for human adjudication. Below ``review_threshold``
   we drop them on the floor.
5. **Apply**    — for an auto-merge pair, pick a deterministic canonical id
   (lex-min of the two) and call :func:`merge_nodes`. Idempotent: re-running
   produces no further changes once everything's merged.

Why not :mod:`dedupe`?
~~~~~~~~~~~~~~~~~~~~~
``dedupe`` is the spec's pick, but it requires a C compiler and didn't
install on this Python (3.14). Same end-state, simpler primitives:
``rapidfuzz`` ships wheels for everything, has zero training data
requirement, and our blocking + threshold scheme is transparent and easy
to audit. The interface to the rest of the system (one clustered Individual
== one node, originals -> aliases) is identical, so swapping in ``dedupe``
later is a drop-in replacement.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from pge.graph.aliases import merge_nodes
from pge.graph.db import GraphDB
from pge.schema.nodes import IndividualNode, node_from_row

# Per-field similarity weights. Must sum to 1.0.
FIELD_WEIGHTS: dict[str, float] = {
    "name": 0.55,
    "employer": 0.25,
    "occupation": 0.10,
    "zip": 0.10,
}


# ---- record extraction ---------------------------------------------------


@dataclass(frozen=True)
class IndividualRecord:
    """Flat view of one ``Individual`` node, used for blocking / scoring."""

    id: str
    name: str
    employer: str
    occupation: str
    zip5: str
    state: str
    raw: dict[str, Any] = field(default_factory=dict)


_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    # Apostrophes (straight and curly) get *dropped*, not space-replaced, so
    # ``O'Brien`` -> ``obrien`` and we don't accidentally split a name into
    # two tokens. Other punctuation collapses to a space.
    s = s.replace("'", "").replace("’", "")
    s = _NORM_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _last_name_token(s: str | None) -> str:
    """Last name. Handles ``'Doe, Jane Q'`` (FEC style) and ``'Jane Doe'`` alike.

    We have to check for the comma **before** :func:`_norm`, which strips
    punctuation -- otherwise we fall through to the trailing-token branch
    and confidently return the middle initial.
    """
    if not s:
        return ""
    if "," in s:
        last_part = s.split(",", 1)[0]
        return _norm(last_part)
    parts = _norm(s).split()
    return parts[-1] if parts else ""


def _first_initial(name: str | None) -> str:
    if not name:
        return ""
    if "," in name:
        rest = name.split(",", 1)[1]
        rest = _norm(rest)
        return rest[:1] if rest else ""
    parts = _norm(name).split()
    return parts[0][:1] if parts else ""


def block_key(name: str) -> str:
    """Coarse key: ``<last><first_initial>``, normalized.

    Two records share a block iff they have the same key. Records with no
    last name fall into the empty-string block, which we skip.
    """
    last = _last_name_token(name)
    if not last:
        return ""
    return f"{last}|{_first_initial(name)}"


def extract_records(db: GraphDB) -> list[IndividualRecord]:
    """Pull every ``Individual`` node into an :class:`IndividualRecord`."""
    records: list[IndividualRecord] = []
    rows = db.conn.execute(
        "SELECT id, kind, name, payload FROM nodes WHERE kind = 'Individual'"
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        records.append(
            IndividualRecord(
                id=row["id"],
                name=row["name"],
                employer=payload.get("employer") or "",
                occupation=payload.get("occupation") or "",
                # IndividualNode doesn't currently store zip/state, but the
                # FEC-derived ones often have them embedded in attrs (left
                # as a hook for future enrichment).
                zip5=str(payload.get("zip5") or "")[:5],
                state=str(payload.get("state") or ""),
                raw=payload,
            )
        )
    return records


# ---- blocking + scoring --------------------------------------------------


def block_records(records: Sequence[IndividualRecord]) -> dict[str, list[IndividualRecord]]:
    """Group records by :func:`block_key`. Empty keys are skipped."""
    blocks: dict[str, list[IndividualRecord]] = defaultdict(list)
    for r in records:
        key = block_key(r.name)
        if key:
            blocks[key].append(r)
    return dict(blocks)


def score_pair(a: IndividualRecord, b: IndividualRecord) -> tuple[float, dict[str, float]]:
    """Compute a weighted similarity in ``[0, 1]`` plus the per-field breakdown.

    Empty fields contribute 0 to that field's term but their weight stays in
    the denominator -- so two records that match only on name still come out
    near 0.55 (the name weight) rather than 1.0.
    """
    name_score = fuzz.token_set_ratio(_norm(a.name), _norm(b.name)) / 100.0

    def cmp(x: str, y: str) -> float:
        if not x or not y:
            return 0.0
        return fuzz.token_set_ratio(_norm(x), _norm(y)) / 100.0

    features = {
        "name": name_score,
        "employer": cmp(a.employer, b.employer),
        "occupation": cmp(a.occupation, b.occupation),
        "zip": 1.0 if a.zip5 and a.zip5 == b.zip5 else 0.0,
    }
    score = sum(features[k] * FIELD_WEIGHTS[k] for k in FIELD_WEIGHTS)
    return score, features


@dataclass
class CandidatePair:
    a_id: str
    b_id: str
    score: float
    features: dict[str, float]


def candidate_pairs(
    records: Sequence[IndividualRecord],
    *,
    min_score: float = 0.5,
) -> list[CandidatePair]:
    """All within-block pairs scoring at or above ``min_score``.

    The pair order is canonicalized so ``(a_id, b_id)`` always has
    ``a_id < b_id``. This keeps ``review_queue`` rows unique under the
    table's PK regardless of comparison order.
    """
    pairs: list[CandidatePair] = []
    for block in block_records(records).values():
        for i, a in enumerate(block):
            for b in block[i + 1 :]:
                a_id, b_id = canonicalize_pair(a.id, b.id)
                if a_id == b_id:
                    continue
                # Re-fetch the records in canonical order so the features
                # apply to the right one. Since score is symmetric, just
                # reuse the score we already have.
                score, features = score_pair(a, b)
                if score >= min_score:
                    pairs.append(
                        CandidatePair(a_id=a_id, b_id=b_id, score=score, features=features)
                    )
    return pairs


def canonicalize_pair(a_id: str, b_id: str) -> tuple[str, str]:
    """Order the pair so ``a_id < b_id`` lexicographically."""
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


# ---- review queue --------------------------------------------------------


@dataclass
class ResolveSummary:
    auto_merged: int = 0
    queued_for_review: int = 0
    skipped_already_decided: int = 0


def queue_for_review(db: GraphDB, pair: CandidatePair) -> bool:
    """Insert a pair into ``review_queue`` if it isn't already there.

    Returns True if a row was inserted, False if a (possibly already-decided)
    row existed.
    """
    cur = db.conn.execute(
        """
        INSERT OR IGNORE INTO review_queue(a_id, b_id, score, features, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        (pair.a_id, pair.b_id, pair.score, json.dumps(pair.features, sort_keys=True)),
    )
    return cur.rowcount > 0


def already_decided(db: GraphDB, a_id: str, b_id: str) -> str | None:
    """Returns 'accepted' / 'rejected' if the pair was previously adjudicated."""
    a_id, b_id = canonicalize_pair(a_id, b_id)
    row = db.conn.execute(
        "SELECT status FROM review_queue WHERE a_id = ? AND b_id = ?",
        (a_id, b_id),
    ).fetchone()
    if row is None:
        return None
    return row["status"] if row["status"] in {"accepted", "rejected"} else None


# ---- apply ---------------------------------------------------------------


def _pick_canonical(a_id: str, b_id: str) -> tuple[str, str]:
    """Return ``(canonical_id, alias_id)``. We keep the lex-min id.

    Stable across runs and trivially tested. A future enhancement would be
    to prefer the node with the most edges incident, but that requires an
    extra query per pair and isn't worth it at v1 volumes.
    """
    a, b = canonicalize_pair(a_id, b_id)
    return a, b  # canonical = a (lex-min), alias = b


def merge_pair(
    db: GraphDB,
    a_id: str,
    b_id: str,
    *,
    source: str,
    confidence: str = "high",
) -> str:
    """Merge two Individual nodes; returns the surviving canonical id."""
    canonical, alias = _pick_canonical(a_id, b_id)
    merge_nodes(db, from_id=alias, to_id=canonical, source=source, confidence=confidence)
    return canonical


def apply_decisions(db: GraphDB) -> int:
    """Process all ``status='accepted'`` review_queue rows: merge each pair.

    Returns the number of merges performed. Rows with status='accepted' that
    refer to already-merged ids are no-ops and don't count.
    """
    rows = db.conn.execute(
        "SELECT a_id, b_id FROM review_queue WHERE status = 'accepted'"
    ).fetchall()
    merged = 0
    for row in rows:
        a_id, b_id = row["a_id"], row["b_id"]
        # If either node is already gone, skip silently.
        exists_a = db.conn.execute(
            "SELECT 1 FROM nodes WHERE id = ?", (a_id,)
        ).fetchone()
        exists_b = db.conn.execute(
            "SELECT 1 FROM nodes WHERE id = ?", (b_id,)
        ).fetchone()
        if not exists_a or not exists_b:
            continue
        if a_id == b_id:
            continue
        merge_pair(db, a_id, b_id, source="review", confidence="high")
        merged += 1
    return merged


def record_decision(
    db: GraphDB,
    a_id: str,
    b_id: str,
    status: str,
    *,
    decided_by: str = "user",
) -> None:
    """Update a queued pair to ``accepted`` or ``rejected``."""
    if status not in {"accepted", "rejected"}:
        raise ValueError("status must be 'accepted' or 'rejected'")
    a_id, b_id = canonicalize_pair(a_id, b_id)
    db.conn.execute(
        """
        UPDATE review_queue
        SET status = ?, decided_by = ?, decided_at = datetime('now')
        WHERE a_id = ? AND b_id = ?
        """,
        (status, decided_by, a_id, b_id),
    )


# ---- top-level orchestrator ----------------------------------------------


def resolve_individuals(
    db: GraphDB,
    *,
    auto_merge_threshold: float = 0.95,
    review_threshold: float = 0.80,
    apply_review_queue: bool = True,
) -> ResolveSummary:
    """End-to-end pass.

    Steps:
        1. Apply any previously-accepted review_queue rows (idempotent).
        2. Extract -> block -> score Individual records.
        3. For each candidate pair:
            * If the pair was previously rejected -> skip.
            * If score >= ``auto_merge_threshold`` -> merge immediately.
            * If score >= ``review_threshold``    -> insert into review_queue.
            * Else                               -> drop.
    """
    if not 0.0 < review_threshold <= auto_merge_threshold <= 1.0:
        raise ValueError("expected 0 < review_threshold <= auto_merge_threshold <= 1")

    summary = ResolveSummary()

    if apply_review_queue:
        pre = apply_decisions(db)
        summary.auto_merged += pre

    records = extract_records(db)
    pairs = candidate_pairs(records, min_score=review_threshold)

    # Sort highest-score-first so the most-confident merges happen before any
    # transitive cluster forms; lower-score pairs are then queued or dropped
    # against the post-merge state.
    pairs.sort(key=lambda p: -p.score)

    for pair in pairs:
        if already_decided(db, pair.a_id, pair.b_id) == "rejected":
            summary.skipped_already_decided += 1
            continue
        if already_decided(db, pair.a_id, pair.b_id) == "accepted":
            summary.skipped_already_decided += 1
            continue

        if pair.score >= auto_merge_threshold:
            # Skip pairs whose nodes have already been merged in this run.
            exists_a = db.conn.execute(
                "SELECT 1 FROM nodes WHERE id = ?", (pair.a_id,)
            ).fetchone()
            exists_b = db.conn.execute(
                "SELECT 1 FROM nodes WHERE id = ?", (pair.b_id,)
            ).fetchone()
            if not exists_a or not exists_b:
                continue
            merge_pair(
                db, pair.a_id, pair.b_id,
                source="rapidfuzz", confidence="high",
            )
            summary.auto_merged += 1
        else:
            inserted = queue_for_review(db, pair)
            if inserted:
                summary.queued_for_review += 1

    return summary


# ---- exposed for review UI / tests ---------------------------------------


def list_pending_review(
    db: GraphDB, *, limit: int = 50, min_score: float = 0.0
) -> list[dict[str, Any]]:
    """Return pending review_queue rows, highest score first.

    Each row includes the two ``IndividualNode`` payloads inline so a UI can
    render side-by-side without further DB hits.
    """
    cur = db.conn.execute(
        """
        SELECT q.a_id, q.b_id, q.score, q.features,
               na.kind AS a_kind, na.payload AS a_payload,
               nb.kind AS b_kind, nb.payload AS b_payload
        FROM review_queue q
        JOIN nodes na ON na.id = q.a_id
        JOIN nodes nb ON nb.id = q.b_id
        WHERE q.status = 'pending' AND q.score >= ?
        ORDER BY q.score DESC
        LIMIT ?
        """,
        (min_score, limit),
    )
    out: list[dict[str, Any]] = []
    for row in cur:
        a_payload = json.loads(row["a_payload"])
        b_payload = json.loads(row["b_payload"])
        out.append(
            {
                "a": node_from_row(row["a_kind"], a_payload).model_dump(),
                "b": node_from_row(row["b_kind"], b_payload).model_dump(),
                "score": row["score"],
                "features": json.loads(row["features"]),
            }
        )
    return out


# Bridge for callers that just want IndividualNodes. Keeps the API
# surface narrow when full payloads aren't needed.
def iter_individuals(db: GraphDB) -> Iterable[IndividualNode]:
    rows = db.conn.execute(
        "SELECT payload FROM nodes WHERE kind = 'Individual'"
    ).fetchall()
    for row in rows:
        yield IndividualNode.model_validate_json(row["payload"])
